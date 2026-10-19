"""The state heartbeat (section 34.3): what the calendar keeps saying.

The review that writes states reads a few minutes at a time, so the same
situation is written again every window it survives - a fortnight of one
project is a hundred states, each true, each worded slightly differently,
and nothing anywhere says "this is one thing". A reader of the calendar
sees a hundred lines; a retrieval over it matches whichever four happen to
share the most words with the question.

So this runs on its own clock and asks the other question: which of the
states already written are about the same thing, and what is that thing?

Three parts, and only the last of them needs a model:

  - how alike two states are, from their words. Two measures rather than
    one: what they *say* (title and detail) and what they *rest on*
    (evidence and sources). Two states can describe the same afternoon in
    different words, or use the same words about different files, and a
    single score cannot tell those apart - so both are computed, reported
    and combined.
  - which of them form a group, by label propagation over the graph those
    scores make. Not clustering into a fixed number of groups: how many
    things the user has been doing is exactly what is being asked, and a
    method that has to be told the answer in advance cannot report it.
  - what each group is, in one summary, written by a model - and only for
    groups big enough to be worth one. A handful of states is a handful of
    states: summarising three costs a call to say roughly what the three of
    them already say.
"""

from __future__ import annotations

import json
import logging
import copy
import math
import os
import re
import threading
import heapq
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ...common.observability.tracing import NullTracer, new_trace_id
from ...common.languages import in_display, in_english, needs_english_copy
from .embedding import Embedder
from ...common.room import Room, at_once
from .tasks import (ACCEPT_AT, AMBIGUOUS_SHARE, AMBIGUOUS_WITHIN,
                    LIVE, SANDBOX,
                    DRIFT_LIMIT, LOOSE_IS_A_TASK, REBUILD_AFTER,
                    RESUMMARISE_AFTER, SAME_TASK_AT, SPREAD_LIMIT,
                    Kept, Task)
from .tasks import append as append_states
from .tasks import apart_by_place, apart_within
from .tasks import OTHER_PLACE_SPECIFIC as _TELLING
from .tasks import match as match_tasks
from .tasks import now as task_now
from .tasks import should_rebuild
from . import todo as _todo
from .tasks import wants_summary
from .tasks import FAR as _FAR
from .tasks import Project as _Project
from .tasks import Projects as _Projects
from .tasks import HALF_LIFE_STATES as _HALF_LIFE
from .tasks import QUIET_AFTER_DAYS as _QUIET_DAYS
from .tasks import RECENT_READ as _RECENT_READ
from .tasks import RENAME_AFTER as _RENAME_AFTER
from .tasks import PLACE_COUNTS as _PLACE_COUNTS
from .tasks import TIE_WITHIN as _TIE_WITHIN
from .tasks import (ARCHIVE_AFTER_DAYS, HALF_LIFE_STATES, LOOSE_KEPT_DAYS,
                    RECENT_READ,
                    REVIVE_AT, Task as _Task, fits as _fits,
                    place as place_states, specificity as _specificity,
                    leaning as _leaning)
from .workspace import ANCHORS_UNDER as _ANCHORS_UNDER
from .workspace import WORDS_AGREE, WORDS_UNDER
from .workspace import anchor as _workspace_anchor
from .workspace import anchor_by_words as _anchor_by_words
from .workspace import keys as _workspace_keys
from .workspace import pull as _workspace_pull
from .workspace import ancestors as _workspace_ancestors
from .workspace import read as _workspace
from .workspace import identifying as _identifying
from .workspace import tools_in as _tools_in
from .hierarchy import _roots
from .hierarchy import (BARELY_TALLER, DEEPEST_LAYERS,
                        ENOUGH_CHILDREN, STANDS_OUT, Merge, cluster,
                        layers, tree)
from ...data_manage.library import Record
from ...data_manage.library.record import STATE_TASKS, STATES
from ...data_manage.text import COMMON_WORDS, estimate_tokens, search_terms
from ...models.base import ModelRequest
from ...models.manager import ModelUnavailable

log = logging.getLogger(__name__)

#: How many states one round will look at, newest first.
#:
#: Every pair is scored, so the work is the square of this - four hundred
#: states is eighty thousand comparisons, which is a fraction of a second
#: of set arithmetic. The bound is here because the square of an unbounded
#: number is an unbounded amount of work, not because four hundred is
#: expensive.
#: How many states one round reads.
#:
#: Raised from four hundred when the grouping stopped being computed from
#: nothing every hour. Four hundred was the square that had to be paid
#: HOURLY; this is the square that is paid when the grouping is found
#: again, which is four times in sixty rounds.
#:
#: Measured, through the code the round runs, on this machine:
#:
#:      states    pairs    scoring   joining   total     peak
#:         400   79,728      1.0 s     1.7 s    2.7 s    21 MB
#:       1,000  498,900      7.3 s    10.6 s   17.9 s   123 MB
#:
#: A thousand costs less now than four hundred did before - the scoring
#: was leaving the tensor library to walk half a million pairs in Python
#: and describing every one of them to print sixty. Both are fixed, and
#: the numbers above are after. Note the shape of the column: it is the
#: square, so two thousand is four times this and five thousand - which
#: the setting still permits - is twenty-five times, several minutes and
#: gigabytes. The bound exists because the square of an unbounded number
#: is unbounded.
MAX_STATES_PER_ROUND = 1000

#: The working-out is shown, and not kept.
#:
#: Every prompt, every reply, every search and everything stripped out of
#: an answer is still collected and still reaches the panel whole - that
#: is what the panel is for. None of it is written to disk any more.
#:
#: It was, for a day, and a day was enough to measure it: seventy files
#: and two and a half megabytes a day, nothing ever deleting one, and
#: about half of each file the same text written twice - the searches
#: repeated the prompts the exchange had already recorded, and the
#: instruction was stored once per call although it is identical in all
#: of them. What is worth keeping is what a round PRODUCED: the task, its
#: summary and its four parts, which are a record in the library and
#: always were. How the model got there is worth looking at once, while
#: it is being looked at, and that is the debugger.

#: The parts a task is described in, beyond its title and its sentence.
#:
#: These are what somebody asks when they come back to something after a
#: week - where was I doing this, what was it for, what is it, how far did
#: I get - and a single paragraph answers all four badly. They are kept
#: apart so they can be updated apart: a round that finds four new states
#: usually moves `status` and nothing else.
DESCRIBED_IN = ("where", "goal", "doing", "status")

#: How common a workspace key may be and still anchor. See workspace.py.
ANCHORS_UNDER = _ANCHORS_UNDER

#: How far a shared ANCESTOR may shorten a distance.
#:
#: Half, at most. A shared folder is evidence and not proof - two papers
#: can sit on one shelf - so it brings a pair near enough that the
#: splitter is asked about them, and does not answer for it.
WORKSPACE_PULL = 0.5

#: How many of the states that moved are put in front of the status call.
#:
#: Eight, against the forty a full summary sees. This call is meant to be
#: cheap enough to make every round: a task that gained thirty states in
#: one round has had a rebuild, and the full summary will run for it
#: anyway on the turnover rule.
STATES_PER_STATUS = 8

#: The state sources this grouping reads.
#:
#: Everything the review wrote from watching, and what the user said
#: themselves. NOT the notes the agent writes to accompany its own
#: scheduled tasks - "Scheduled: check the FieldConf 2027 deadline" is a real
#: record and belongs on the calendar, but it is not something the user
#: did. Twenty of them say the same thing in twenty wordings, because the
#: agent kept rescheduling one reminder, so they group tightly and
#: perfectly - and a task called "Check the submission deadline, and where
#: the two documents were saved" is the agent's own backlog handed back to
#: the user as though it were their work.
#: Named from the store rather than repeated here, so the two cannot
#: drift: a source added there and forgotten here would be dropped from
#: every grouping without anything saying so.
from ...agent.states.store import SOURCE_AGENT as _OBSERVED_AGENT
from ...agent.states.store import SOURCE_USER as _OBSERVED_USER
from ...agent.states.store import LEISURE, WORK

OBSERVED = (_OBSERVED_USER, _OBSERVED_AGENT)

#: How long after start-up the first round waits, in seconds.
#:
#: Long enough that start-up is not holding it, short enough that a fresh
#: install does not have an empty panel until the top of the hour.
FIRST_DELAY_SECONDS = 60

#: How much looser than the loosest real task a candidate out of the pile
#: may be, and how tight it must be when there are no tasks to judge by.
#:
#: A quarter again, because the tasks on a real calendar sit between 0.10
#: and 0.21 from their centres and the difference between the tightest
#: and the loosest is already twice that - so a candidate at the loose end
#: is ordinary, and one much past it is not one thing.
#:
#: Both numbers describe an EDGE - middle plus twice the spread - and not
#: a middle, so that a group of four identical states and two strangers
#: is measured by the strangers rather than by the four.
PILE_ALLOWANCE = 1.25
FARTHEST_PILE = 0.45

#: How far from its own middle, on average, a group out of the pile may sit:
#: this much past the widest task the model has accepted as one piece of
#: work, or the second number when none has been judged yet.
#:
#: The edge alone did not separate them. Measured on a real calendar, the
#: two groups the pile made that the model then called "an overview of the
#: user's research and personal life" had edges of 0.58 and 0.59, inside
#: the 0.60 allowed - but their middles were 0.42 and 0.43, against 0.25 to
#: 0.32 for every task the model accepted. A group that wide is a pile of
#: several things, and it takes in whatever fits nowhere else for good.
PILE_MIDDLE_ALLOWANCE = 1.1
FARTHEST_PILE_MIDDLE = 0.35

#: How deep to go looking for something worth showing.
#:
#: Two, which is as deep as the layers go: a branch with nothing to
#: finish by the second level has nothing to finish, and each level costs
#: a model call per piece.
DEEPEST_SUMMARY = 2

#: How many states a SANDBOX round reads.
#:
#: A hundred, against the real thousand. The sandbox exists to be
#: watched, and a rehearsal over a thousand is three clusterings and a
#: model call per task before it says anything. A hundred still makes
#: several tasks with pieces inside them - which is what there is to
#: look at - and finishes while somebody is still looking at it.
SANDBOX_STATES_PER_ROUND = 100

#: The sandbox's own numbers, in one place.
#:
#: NOT SETTINGS. They were, for an afternoon, and that was long enough for
#: an install to write them into its settings file - where a stored value
#: beats a schema default for ever, so the sandbox went on reading a window
#: that had already been replaced. How much made-up data a debugging tool
#: reads is not a fact about the user's agent and does not belong on a page
#: they configure.
#:
#: They are still read through `config.get(key, fixture)`, so a test or
#: anyone debugging the debugger can set one - it simply is not offered,
#: and is never written down as a default.
SANDBOX_FIXTURES = {
    "state_sandbox_max_states": SANDBOX_STATES_PER_ROUND,
    "state_sandbox_summaries": True,
    "state_sandbox_root": "",
}

#: How far back the FIRST grouping looks, in days.
#:
#: A month, because a first grouping is answering "what have I been
#: doing", and a year of states is not that question. What is older is
#: not read and is not a backlog - see `_reading`.
FIRST_ROUND_DAYS = 30

#: How many terms are taken from each side of a state.
#:
#: The title and detail of a state are a sentence and a paragraph; the
#: evidence and sources are a handful of paths and URLs. Both are small,
#: and the cap is only a guard against a state whose sources are a
#: thousand files.
TERMS_PER_SIDE = 120

#: How much of the group each summary call is shown.
STATES_PER_SUMMARY = 40

#: How many of the scored pairs the report carries in full, highest first.
#:
#: The table. Every pair that shared anything is scored - eighty thousand
#: of them at four hundred states - and a table of eighty thousand rows is
#: a table nobody reads.
PAIRS_IN_REPORT = 60

#: The floor between consecutive rounds when one is asked for by hand.
QUIET_SECONDS = 5


SUMMARY_PROMPT = """SUMMARISE WHAT THIS GROUP OF STATES IS ABOUT.

  WHATEVER LANGUAGE YOU WRITE IN, THE KEYS OF THE JSON STAY EXACTLY AS WRITTEN. `title`, `summary`,
  `where`, `goal`, `doing`, `status` and the rest are the names of fields in
  a program, not words on a page. Translating them produces an object
  nothing can read, and the summary is silently lost.

  AND SO DOES ANYTHING QUOTED FROM THE STATES. File paths, folder names,
  repository names, identifiers, the titles of papers, the names of people
  and sites. "survey-weights/scripts/fetch.py" is a path and not a phrase;
  translating it names a file that does not exist.

Each state below was written by an earlier review, from a few minutes of the
user's activity. They have been grouped by what they MEAN: each was read as a
vector and the closest were joined, so two states can be in this group without
sharing a single word. Your job is to say what the group IS.

ONE SUMMARY OF THE GROUP, NOT A LIST OF ITS MEMBERS. If the answer you would
write is "they were doing A, then B, then C", the group is that sequence and
the summary says what the sequence was in aid of. A summary that reads as a
list is a list with a paragraph around it, and the list is already below.

THE TITLE IS AN INSTRUCTION, NOT A DESCRIPTION. Not a phrase and not a
label either: something that reads on its own, because it is the line
somebody sees with nothing around it.

SAY WHAT IS SHARED AND WHAT IS NOT. A group is rarely uniform: if most of it
is one thing and two of them are something else that happened to use the same
words, say so. That is the useful part of a summary of many things, and it is
the part a title cannot carry.

DO NOT INVENT A CONNECTION. Being near in meaning is evidence and not proof:
a measure of similarity put these together, and it has no idea what the user
was doing. If the only thing they share is a subject rather than a purpose -
five sittings that are all "reading something" - the honest summary says that,
and says the group is thinner than it looks.

NEVER PUT THE TIME IN THE TITLE OR THE SUMMARY. Not "over twelve days", not
"across three afternoons", not "this week". Every state below carries the
moment it covers, so the span of the group is arithmetic on those moments -
it is worked out and shown beside your words, and writing it yourself adds
nothing and will disagree with the arithmetic sooner or later. Say WHAT the
work was. The calendar says when.

IS THERE PROGRESS TO TRACK HERE? Say so in `concrete`.

This is the whole test for whether the user is shown this group at all, and
it is narrower than "is it true". Three kinds of group come out of this
grouping and only one of them is a task:

  A TASK has something to finish and somewhere to get to. Rebuilding a
  package, answering a reviewer, getting a visa, reading a particular series
  through. You could ask "how far along is it" and the question would mean
  something. THIS is shown.

  A STATE is what the user is like or what they were doing, with nothing to
  finish. "Clearing out the Gmail inbox" is a state: the inbox is never
  finished, it is just where mail goes. So is "reading the news", "watching
  videos in the evenings", "keeping up with messages". Recording it is
  useful to this application and showing it to the user is not - a list of
  what somebody is working on with "clearing out the inbox" in it is a list
  they will stop reading.

  A WAY OF WORKING is a pattern in how the time was spent: returning to
  something repeatedly, short sittings, evenings, being interrupted. Also
  true, also not a task.

`concrete` is TRUE only for the first kind. Set it false for the other two and
say which in `concrete_why`.

BEING ABOUT ONE SUBJECT IS NOT ENOUGH. Gmail is a subject; answering a
particular reviewer about a particular paper is a task. If everything here is
one subject and nothing in it has an end, that is a state, and the answer is
false even though you can name the subject.

AND IF THE GROUP IS A STATE WITH A TASK BURIED IN IT, say so in
`concrete_why` and put it in `buried`: a title for that task, and the
NUMBERS of the states shown above that are about it. Those states are taken
out and made a task of their own, which is shown - so "mostly the inbox,
but several of these are about the visa letter" is the answer that gets the
visa letter found. Give the numbers of every shown state that belongs to it
and no others; one task only, the clearest; and leave `buried` out when
`concrete` is true or nothing in the group has an end.

HOW TO WRITE THE TITLE. AN IMPERATIVE, and a specific one.

The user reads these. A list of things somebody is doing reads better as a
list of things to do: "Check the figures" is read at a glance, and "They are
checking the figures" makes the reader convert it before they can use it. So
write the verb first and leave the user out of it - they know who they are.

AND NAME THE PARTICULARS. Which paper, which package, which person, which
trip. "Against the ones published in the paper" is useless a month later;
"against the ones in the SWR paper" can be acted on. Take the
names from the states - they are full of them - and if the states do not say
which thing it was, say that rather than writing a vague noun.

  GOOD  Check the figures in the SWR replication package against the ones
        published in "Weighting rare groups in national surveys".
  GOOD  Fix the census fetch step in survey-weights/scripts/fetch.py so
        the replication runs end to end.
  GOOD  Answer reviewer 2 on the weighting paper's method section.
  GOOD  Finish the visa letter and expense claim for the FieldConf workshop in Lisbon.
  GOOD  Watch the YouTube series on the history of city planning.

Each of those names the thing a month later: which paper, which file, which
reviewer, which trip. "The paper" and "the scripts" name nothing - by the
time the user reads this they have worked on four papers.

  BAD   They are checking the figures in a replication package against the
        ones published in the paper.
                    (not an imperative, and "the paper" names nothing)
  BAD   Check the figures.                       (which figures? whose?)
  BAD   Replication work.                        (not an instruction at all)
  BAD   Do the paperwork.                        (which paperwork, for what?)
  BAD   Spend twelve days on the replication.    (the time is not yours)
  BAD   Keep returning to one job in short sittings.
                                    (a way of working - `concrete` false)
  BAD   Clear out the Gmail inbox.
                     (a state - nothing to finish - `concrete` false, even
                      though it is an imperative and names the subject)
  BAD   Fix the fetch step and re-run the scripts and check the figures.
                     (three titles in one. A title says the ONE thing this
                      group is; if it needs an "and", the group is being
                      described from too low down - go up until it does not)

If the states genuinely do not name the thing - they say "the paper" and
never which - then write what they do say and put the vagueness in the
summary: "the states do not say which paper". Do not invent a name.

HOW TO WRITE THE SUMMARY. Also an imperative, then the particulars. Three
things in this order: what to do, what it is in aid of, and where its edges
are - which of the states do not fit and why they are here anyway.

ONE STATEMENT, AT ONE ALTITUDE. This is the rule that is easiest to break
and hardest to see broken. You are shown twenty or forty states and the
temptation is to say all of them - "they fixed the fetch step, then re-ran
the scripts, then checked the figures, and also wrote back to the reviewer"
- which is the list of states again, in one sentence, with the states' own
dates taken off. It is not a summary. A summary is the ONE THING all of
those were: name it, and the reader can see the twenty underneath it at any
time by opening the task.

So: say what it is at the altitude where it is one thing. If you find
yourself joining clauses with "and", "then", "as well as" or a semicolon,
you are listing rather than summarising - go up a level until the list
becomes a single noun. Name a particular step only where it is the ONE
thing the whole group is about, never as an item in a series.

  GOOD  Rebuild the replication package for the SWR paper so the
        figures match the published ones. It is for a revision - the later
        states move from the code into the text of the results section. Two
        of the states are about a different paper's figures and sit here only
        because they also say "figures".
                  (one thing, named; the individual sittings are underneath)

  GOOD  Finish the census fetch step in the replication scripts.
                  (a particular step, because that is what the whole group
                   is about, not one item of several)

  BAD   Fix the census fetch step, re-run the scripts, check the figures
        against the published ones, and write back to the reviewer.
                  (four things joined by commas: this is the list of states
                   with the dates removed. What were all four FOR?)

  BAD   They were working on the replication package, then moved to the
        results section, and then to the reviewer's letter.
                  (a narration of the order things happened in. The order is
                   in the states' dates, and the tree draws it)

  BAD   The user worked on this repeatedly, returning to it in short sittings
        between other things.
                  (how and when, not what; not an instruction; no particulars)

AND THEN THE SAME TASK IN FOUR PARTS. The title and the summary are the
sentence a person reads first; these four are what they ask next, when they
sit down again a week later and cannot remember where they were.

HOW THESE FOUR ARE WRITTEN, WHICH IS NOT HOW THE TITLE IS WRITTEN.

  THE TITLE AND THE SUMMARY ARE INSTRUCTIONS TO THE USER: "Check the figures
  in the SWR replication package." The verb comes first and the user is left
  out, because they are the person being addressed and they know who they
  are.

  THESE FOUR ARE STATEMENTS ABOUT THE USER: "The user is rebuilding the
  replication package for the SWR paper." Complete sentences,
  with a subject, a verb and an object, and THE SUBJECT IS ALWAYS "the
  user". Not "they", not a bare noun phrase, not a participle hanging off
  nothing. A field reading "a revision that a reader can reproduce" is a
  fragment: it does not say who is doing what, and the person reading it
  has to supply the half that was left out.

  Both are written for the user to read. The difference is that one tells
  them what to do and these four tell them what is going on.

AND SAY HOW IT CAME ABOUT. Each of these should leave the reader able to
answer "why is this being done, and what happened so far" without opening
anything. A field that states a fact and not its reason - "The fetch step
fails" - is a line the user has to reconstruct the context for. "The user
cannot finish the rebuild because the fetch step fails on the census API rate
limit" is the same fact with its cause attached, and it can be acted on.

  where      WHERE THE WORK LIVES: the folder, repository, file, site or
             application the user would have to open to carry on. Take it
             from the files and pages the states name, and give the most
             specific thing that covers most of them. Paths and site names
             keep their exact form.

             e.g. "The user does this work in C:/Research/survey-weights/,
                   mostly in scripts/fetch.py and the figures notebook, and
                   reads the published version from the SWR PDF in the same
                   folder."

             e.g. "The user does this on openreview.net, in the FieldConf 2027
                   author console, and keeps the draft in
                   C:/Papers/fieldnotes/."

  goal       WHAT THE USER IS TRYING TO BRING ABOUT, and why it matters to
             them. State the thing that will be true when this is over, and
             what depends on it. Not the title in other words: if the goal
             reads like the title with more adjectives, it is not a goal,
             it is a restatement.

             e.g. "The user wants the replication package to reproduce every
                   figure in the published SWR paper from a clean checkout,
                   because the journal asked for a package a referee can run
                   unaided before the revision is accepted."

             e.g. "The user wants to settle the Lisbon trip so it stops
                   taking up attention: the visa letter has a deadline that
                   falls before the conference, and the expense claim has to
                   go in while the receipts are still valid."

  doing      WHAT THE WORK ACTUALLY CONSISTS OF, in prose, at the altitude
             of the task rather than of one sitting. This is the one field
             where naming several parts is right - it is a description, not
             a claim - so say what the parts are and how they relate.

             e.g. "The user is re-running the whole pipeline against a fresh
                   census pull and then reconciling each figure against
                   the published one. The fetch step has to work before any
                   of the figures can be checked, so most of the sittings
                   have gone on that rather than on the figures themselves."

             e.g. "The user is answering the reviewer point by point, and
                   each answer needs a number out of the rebuilt package, so
                   the letter is waiting on the replication rather than on
                   the writing."

  status     WHERE IT STANDS AND WHAT IS LEFT, read off the most recent
             states. Say what is done, what is open, what is blocking, and
             what the next step is where the states say it. Dates are not
             needed - the calendar has them - but "still", "now" and "since"
             are, because the reader wants to know whether this moved.

             e.g. "The user has the pipeline running end to end and figures
                   1 and 3 now match the published ones. Figure 2 is still
                   out, and the fetch step keeps failing on the census API
                   rate limit, which is what the last three sittings have
                   been spent on."

             e.g. "The user has drafted the letter and has not sent it,
                   because two of the answers still need numbers from the
                   rebuilt package."

  BAD across all four, and these are the shapes to avoid:

    BAD   "a revision a reader can reproduce"
                  (a fragment - no subject, no verb; who is doing what?)
    BAD   "Re-running the pipeline and checking the figures."
                  (a participle hanging off nothing, and the user is absent)
    BAD   "They are rebuilding the package."
                  ("they" - the subject is always "the user")
    BAD   "The fetch step fails."
                  (a fact with its cause and its consequence both missing)
    BAD   "The user is working on the replication."
                  (true of every state in the group and of nothing in
                   particular - it names no file, no paper and no obstacle)

AND TWO NUMBERS: HOW MUCH IT MATTERS, AND HOW SOON.

Both run 1 to 7. They are separate questions and a task can be high on one
and low on the other - that is the point of asking twice.

  urgency     HOW SOON IT NEEDS DOING. Read it off the states, not off
              your sense of the subject.

              7  a named deadline inside a few days, or something is
                 blocked on this right now
              6  a deadline this week, or someone is waiting on a reply
              5  moving fast and unfinished - several sittings in the last
                 few days, each carrying on from the last
              4  live, with no date attached: worked on lately, no sign of
                 anything waiting
              3  slow - touched occasionally, nothing hanging on it
              2  quiet for a while; it would keep
              1  finished, or abandoned, or waiting on somebody else with
                 nothing the user can do

  importance  HOW MUCH RESTS ON IT. Not how much time it has taken - a
              long job can be minor and an hour can matter enormously.

              7  a commitment to someone else with consequences: a
                 submission, a review, an application, a legal or medical
                 thing
              6  central to the user's work, with other things depending
                 on it
              5  real work with a purpose, affecting nobody else
              4  worth finishing, but nothing follows from it
              3  useful, easily dropped
              2  routine upkeep
              1  leisure, browsing, incidental

  weighed_why one sentence for each number, saying what in the states
              decided it. "The visa letter has a deadline before the
              conference, and the expense claim has to go in while the
              receipts are valid" is a reason; "it seems important" is not.

  WORKED EXAMPLES, so the scales mean the same thing twice.

    "Answer reviewer 2 on the weighting paper before the revision is due
     on the 14th" - urgency 7, importance 7. A date, and a person waiting.

    "Rebuild the replication package so the figures match the published
     ones" - urgency 5, importance 6. Several sittings running, nothing
     dated; the submission depends on it.

    "Finish the visa letter and the expense claim for Lisbon" - urgency 6,
     importance 5. The letter has a date; nobody's work depends on it.

    "Read through the survey-weighting literature" - urgency 3,
     importance 5. Real work, no date, nothing waiting.

    "Watch the YouTube series on the history of city planning" - urgency 2,
     importance 1. Leisure, and it will keep.

    "Clear out the Gmail inbox" - urgency 3, importance 2. Upkeep.

  IF THE STATES DO NOT SAY, answer 4 and say so in `weighed_why`. A
  middling number with "the states give no sign either way" is honest; a
  7 chosen because the subject sounds weighty is not.

WHAT IS ALREADY KNOWN, AND WHAT TO DO WITH IT. If the message includes a
section headed ALREADY KNOWN, this task has been described before and you
are being shown that description. You are looking at a group that has since
gained states.

  RETURN ONLY THE PARTS THAT HAVE CHANGED. Leave a part out and it is kept
  exactly as it was. This is not an optimisation - it is the point. A round
  where four states arrived usually changes `status` and nothing else, and
  rewriting the other three would be three claims restated by something
  that had no new reason to restate them, in different words, so that a
  reader watching the page sees the description of unchanged work change
  every round.

  So: read what is already known, read the new states, and write the part
  that is now wrong. If `where` has not moved, leave `where` out. If the
  work is the same work and only its state has moved on, send `status`
  alone. Send `title` again only if the task has genuinely become a
  different thing - and if it has, say so in `changed_why`.

  EXCEPT `concrete` AND `concrete_why`, which you send EVERY time. They are
  a judgement of the group as it stands now, and new states can change it -
  a group that looked like a state can turn out to have an end, and the
  other way round. So judge it again each time, from what you are shown.

WHAT YOU HAVE IS INCOMPLETE, IN TWO WAYS AT ONCE. Know this before you
conclude anything from its absence.

  NOT ALL THE STATES. Where a group is larger than the budget you are shown
  one state from each cluster of it, with a number saying how many it stands
  for. The others exist and you have not seen them.

  NOT ALL OF EACH STATE. You get a title, a detail, when it ran, and up to
  eight each of the files it rested on and the evidence it recorded. Every
  state ALSO has the reasoning the review wrote it with, its status, and the
  whole of its evidence and sources. None of that is in front of you.

SO "THE STATES DO NOT NAME IT" IS NOT SOMETHING YOU CAN SEE FROM HERE. It is
a thing you can only find out by looking. Writing "the states do not name
which paper" or "no particular site or series is named" from this sample is
reporting the sample's shape as a fact about the user's work, and it is
usually wrong: the name is in the evidence, or in the reasoning, or in one of
the states standing behind a number.

If you are about to write that something is not named - SEARCH FOR IT
INSTEAD. Then write what you found, or, if the search comes back empty, say
it is not named and you will be right.

To look, reply with ONLY this and nothing else:

{"need": {"keywords": ["weighting", "SWR"], "ids": ["the id of a state"]}}

  keywords - searched over EVERY state in this group, including the ones
             standing behind a number that you were not shown. Use the words
             you would expect the answer to be written near: a word is
             found only as it is written.
  ids      - the square-bracketed id of any state above, returned in full:
             its reasoning, all of its evidence and all of its sources,
             rather than the eight of each you were shown.

THOSE IDS ARE FOR ASKING WITH AND FOR NOTHING ELSE. They are random strings.
The user never sees one, cannot look one up, and would learn nothing from it.
So no id appears in your title, your summary, your reasoning or your evidence
- not in brackets, not as a reference, not as "see rec_4f2a". Name the thing
itself: the file, the paper, the page, the person, or the words the state
used.

  GOOD  evidence: ["the sittings on fetch.py", "the figure notebook"]
  BAD   evidence: ["rec_4f2a91", "state_b7e3a9"]
  BAD   summary: "...as in rec_4f2a91 and the three states after it."

The search reaches only the states in THIS group. It cannot see the rest of
the calendar, and there is nothing else to ask it for.

You may do this at most twice, and then you must answer. If a search comes
back empty, then and only then is "the states do not name it" a finding
rather than a guess - and say that you looked.

Return ONLY this JSON object and nothing else. On a task that has been
described before, send the parts that changed and leave the rest out; on one
being described for the first time, send all of them.

The KEYS below are field names and are written exactly as they appear here,
whatever language you are writing in. The VALUES are what the user reads and
go in their language - except for paths, identifiers and anything quoted from
the states, which keep the form they have.
{
  "title": "AN IMPERATIVE - the verb first, the user left out - naming the "
           "particular paper, file, person or trip. No duration.",
  "summary": "ONE high-level sentence, also imperative: the one thing all "
             "of these were. Then, in later sentences, what it is in aid "
             "of and which members do not fit. No duration.",
  "where": "A SENTENCE STARTING 'The user' - where the work lives: the "
           "folder, file, repository or site they would open to carry on. "
           "Empty if the states do not say. Paths keep their exact form.",
  "goal": "A SENTENCE STARTING 'The user' - what they are trying to bring "
          "about, and what depends on it. Not the title reworded.",
  "doing": "SENTENCES STARTING 'The user' - what the work consists of and "
           "how its parts relate. The one field where naming several "
           "things is right.",
  "status": "SENTENCES STARTING 'The user' - what is done, what is open, "
            "what is blocking it, and what is next where the states say.",
  "urgency": 1 to 7 - how soon it needs doing, by the scale above,
  "importance": 1 to 7 - how much rests on it, by the scale above,
  "weighed_why": "one sentence per number: what in the states decided it",
  "changed": ["the names of the fields above you are sending because they "
              "changed, when this task was already described. Omit on a "
              "first description."],
  "changed_why": "one sentence, only when something other than `status` "
                 "changed: what in the new states made it wrong.",
  "concrete": true or false - true ONLY when there is something to finish
              here and "how far along is it" is a question with an answer;
              false for a state and false for a way of working,
  "concrete_why": "one sentence: what there is to finish, or why there is "
                  "nothing - and if a task is buried in a state, what it is",
  "buried": {"title": "ONLY when concrete is false and a task is buried "
                      "here: its title, written as titles are above",
             "states": ["the NUMBERS of the shown states about it"]},
  "not_this_work": ["the NUMBERS of the shown states that are plainly about "
                    "something else - other work, a chat, browsing or a "
                    "pastime on another subject - and so not this task. Only "
                    "one clearly about something else: a state that could be "
                    "this work stays. [] when every one belongs."],
  "reasoning": "why these belong together, in terms of what you can see",
  "evidence": ["what this rests on, named by its content - a file, a page, "
               "a paper, or the words a state used. NEVER an id."]{in_english}
}"""

#: A task's English copy, asked for only when the user does not read English
#: (languages.needs_english_copy): what it is measured and compared by.
SUMMARY_ENGLISH = """,
  "in_english": {"title": "...", "summary": "...", "where": "...", "goal": "...",
                 "doing": "...", "status": "..."} - THE SAME FIELDS YOU SEND
                 ABOVE, IN ENGLISH, for the agent's own use: only those you
                 send, said the same way. ONLY WHAT IS NOT IN ENGLISH ALREADY:
                 a field you wrote in English is not repeated here, and when
                 every one is in English, leave "in_english" out altogether -
                 each word you write is paid for."""


STATUS_PROMPT = """SAY WHERE THIS TASK STANDS NOW.

You are not summarising it again. It has a description already, it is below,
and it is almost certainly still right: the work is the same work, the place
it lives has not moved, and what it is in aid of has not changed. What may
have changed is the LAST of the four - where it stands and what is left -
because the states in this task have changed since that description was
written. What changed is below too.

Read what moved and answer one question: given what was already known, what
is the state of this work now?

  STATES MAY HAVE ARRIVED, AND STATES MAY HAVE GONE. An arrival is more of
  this work happening. A departure is the grouping deciding that a state
  belongs to something else - the task is about less than it was, and a
  status describing the larger thing is now wrong. Both are shown, and both
  can move the status.

  SAY IT AS A CHANGE WHERE IT IS ONE. "The user now has figure 1 matching"
  is worth more than "the user has figure 1 matching", because the reader
  has seen the old status and wants to know what moved.

  AND SAY SO WHEN NOTHING MOVED. If what changed is more of the same -
  another sitting on the same obstacle, the same thing tried again - then
  the status has not changed and you should say so, with `changed` false.
  Rewriting an unchanged status in new words is the thing this call exists
  to avoid.

  KEEP THE OTHER THREE OUT OF IT. You are not asked for `where`, `goal` or
  `doing` and must not return them. If what changed suggests one of those is
  now wrong - the work has moved somewhere else entirely, or it is in aid of
  something different - say that in `why` and leave it to the full summary,
  which has every state in front of it and not only the ones that moved.

Complete sentences whose
subject is "the user", saying what happened and why, exactly as the
description below is written. Paths, identifiers and quoted names keep
their form.

Return ONLY this JSON object and nothing else:
{
  "status": "A SENTENCE OR TWO STARTING 'The user' - what is done, what is "
            "open, what is blocking it, and what is next where the states "
            "say. Omit this field entirely if nothing has changed.",
  "changed": true or false - whether the status you were shown is now wrong,
  "why": "one sentence: what in the states that moved changed it, or why "
         "nothing did",
  "not_this_work": ["the NUMBERS of the ARRIVED states (arrived 1, arrived "
                    "2...) that are plainly about something else - other "
                    "work, a chat, browsing or a pastime on another subject "
                    "- and so not this task. Only one clearly about something "
                    "else: a state that could be this work stays. [] when "
                    "every one belongs."],
  "urgency": 1 to 7 - HOW SOON THIS NEEDS THE USER, not how much it
             matters. 7 a deadline inside a day or something already
             late; 6 a fixed date this week; 5 someone is waiting on it;
             4 it is moving and nothing outside it is pressing; 3 it has
             a date but a distant one; 2 it can wait indefinitely; 1
             nothing about it is time-bound. A status that says someone
             is waiting, or names a date, MOVES THIS - which is why it
             is asked here and not only when the whole summary is
             written.
  "importance": 1 to 7 - WHAT TURNS ON IT, whatever its timing. 7 the
                user's central work; 6 a large commitment to someone
                else; 5 real work with real consequences; 4 ordinary
                work; 3 useful but nothing waits on it; 2 a small
                convenience; 1 idle. This rarely moves round to round:
                return what the description already implies unless the
                states say otherwise.
  "weighed": "one sentence, in the user's language, saying what in the
              task put those two numbers where they are"{status_in_english}
}

GIVE BOTH NUMBERS EVERY TIME, even when `changed` is false: a task can be
no further along than it was and more urgent than it was, because a date
came closer while nothing happened. WHERE THE STATES GIVE NO SIGN, 4 AND
4 AND SAY SO - a middling number with a reason is honest, and a 7 chosen
because the subject sounds weighty is not."""


#: A status's English copy, asked for only when the user does not read English.
STATUS_ENGLISH = """,
  "status_in_english": "the status above, in English, for the agent's own use -
                        only when you send a status, and
                        not when it is in English already: then leave this out,
                        since each word you write is paid for\""""


PLAN_PROMPT = """PUT THESE PIECES OF ONE JOB IN ORDER.

The keys of the JSON, and the numbers, are not words on a page and do not
change.

Below are the sub-tasks of a single task, each with when its states began and
ended, how many states it holds, and what it was summarised as. They are
numbered.

Answer two things.

WHICH MUST BE DONE BEFORE WHICH. A dependency is not "this happened first".
It is "this could not have been done until that was" - the figures cannot be
checked until the script that makes them runs; the paper cannot be submitted
until it is written. Two pieces that merely happened in some order, and could
equally have happened in the other, have NO edge between them. Most pairs have
no edge. An empty list is a perfectly good answer and a better one than a
chain invented to look tidy.

WHICH ARE FINISHED. Finished means there is nothing left to do in it, not that
it stopped being worked on. You are told when each one's states end and
whether the states themselves were closed; a piece whose states all ended a
fortnight ago and were closed is finished, and one that simply has not been
touched this week is not.

Return ONLY this JSON object and nothing else:
{
  "depends": [[2, 1], [3, 1]] - each pair is [this one, cannot start until
             this other one is done]. Use the numbers shown. Empty if none.
  "finished": [1, 4] - the numbers of the ones with nothing left to do.
  "why": "one sentence on the order you chose, and what you had to guess"
}"""


#: Asked when new states look like a task that has been dormant, and share
#: none of its places - so only the wording says they are the same work.
SAME_WORK_AGAIN_PROMPT = """IS THIS THE SAME WORK, PICKED UP AGAIN?

Below is a task the user worked on earlier, which has been dormant since
the date given, and new states that read like it. Say whether the new
states are that task resumed - the same piece of work, carried on - or
something new that only sounds alike: a different paper, a different
project, the same kind of activity about another thing.

Return ONLY this JSON object:
{"same": true or false, "why": "one sentence"}"""

#: Asked when several tasks live in the same place - the same project
#: folder, the same paper - and may be one larger piece of work.
ONE_PROJECT_PROMPT = """ARE THESE TASKS PARTS OF ONE LARGER PIECE OF WORK?

The tasks below were found separately, from what the user was doing, and
they share where the work lives - the same project folder, the same
document, the same site. Say whether some or all of them are parts of one
larger piece of work: one project, one paper, one job, of which each task is
a part. Sharing a place is not enough on its own - a person's mail is where
many unrelated things happen, and so is a general-purpose site. And a task
that only happens AROUND the work, rather than being part of it, is not a
part of it.

Give the numbers of the tasks that belong together, and a short name for
the larger piece of work - what the user would call it.

Return ONLY this JSON object:
{"one_project": true or false,
 "tasks": [1, 2, 3] - the ones that belong to it,
 "name": "a short name for the larger piece of work",
 "why": "one sentence"}"""

#: Asked when one of a project's tasks has moved, and once to begin with.
PROJECT_STATUS_PROMPT = """SAY WHERE THIS PROJECT STANDS AS A WHOLE.

Below is a project - a larger piece of work - and its tasks, each with how
urgent and important it is and what has most recently happened in it. Write where the project as a
whole stands now: what is done, what is in progress, and what is next, in a
sentence or two starting 'The user'. Do not list the tasks; say what they add
up to.

{rename}{todo}Return ONLY this JSON object:
{"status": "a sentence or two starting 'The user'",
 "name": "the project's name - the one it has, unless asked to reconsider",
 "why": "one sentence",
 "todo": ["its next steps, by the four rules above - the whole list, every
          time"],
 "todo_note": "one sentence, only where you went against what the user
               changed: what, and why"{in_english}}"""

#: A project's English copy, as a task's.
PROJECT_ENGLISH = """,
 "in_english": {"name": "the name above, in English",
                "status": "the status above, in English"} - for the agent's own
 use; leave out a part already in English, and the whole of it when both
 are - each word you write is paid for"""

RENAME_NOTE = """Its tasks have changed a good deal since it was named, so
consider its name too: keep it if it still says what the project is, and
give a better one if it does not.

"""


#: Asked when a task newly shares a project's places.
JOINS_PROJECT_PROMPT = """IS THIS TASK PART OF THE PROJECT?

Below is a project - a larger piece of work and the tasks already part of
it - and tasks that have come to share its places. Say which of them are
parts of the project. A task that only happens around it, or only shares a
general-purpose place with it, is not.

Return ONLY this JSON object:
{"tasks": [1, 2] - the numbers of the new tasks that are part of it,
 "why": "one sentence"}"""


#: Asked when two tasks have come to sit on top of each other.
SAME_TASK_PROMPT = """ARE THESE TWO TASKS THE SAME WORK?

Two tasks have come to sit very close together. Say whether they are one
piece of work found twice - in which case they will be joined - or two
different things that happen to be worded alike.

Return ONLY this JSON object:
{"same": true or false, "why": "one sentence"}"""


class _Where:
    """Which states a round reads, and where its tasks go.

    The whole difference between a real round and the debugger's is this
    object. The algorithm is not branched on, not copied and not
    reimplemented: `run_once` is handed one of these and does the same
    thing either way, because a debugger that runs different code from
    the thing it is debugging is only testing itself.
    """

    def __init__(self, name: str, states: Any, library: Any,
                 scope: str) -> None:
        self.name = name
        self.states = states
        self.library = library
        self.scope = scope

    def to_dict(self) -> Dict[str, Any]:
        root = getattr(self.library, "root", "")
        return {"name": self.name, "scope": self.scope,
                "library": str(root) if root else "",
                "states_from": str(Path(root) / STATES) if root else "",
                "tasks_to": str(Path(root) / STATE_TASKS) if root else ""}


class _SandboxStates:
    """The states in a folder, read as states.

    The real store keeps states in the database and mirrors them to disk.
    The sandbox has no database of its own: the folder IS the record,
    because the point of it is that a person can look at what went in,
    change it, and run the grouping over it again.
    """

    def __init__(self, library: Any) -> None:
        self.library = library
        #: Each file's text, by path, with the (mtime, size) it was read at.
        #: A lookup read every state file in the folder - fourteen lookups
        #: at start-up, ninety files each, eight seconds - so a file is read
        #: again only when it has changed. Parsed afresh every time: what
        #: is handed out is never an object somebody else holds.
        self._texts: Dict[str, Tuple[Tuple[int, int], str]] = {}

    def _records(self) -> List[Any]:
        from ...data_manage.library.record import Record

        out: List[Any] = []
        seen = set()
        for path in self.library.files(STATES):
            name = str(path)
            seen.add(name)
            try:
                info = os.stat(path)
            except OSError:
                continue
            shape = (info.st_mtime_ns, info.st_size)
            held = self._texts.get(name)
            if held is None or held[0] != shape:
                try:
                    text = Path(path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                held = self._texts[name] = (shape, text)
            out.append(Record.from_text(held[1], folder=Path(path).parent.name,
                                        path=name))
        for gone in set(self._texts) - seen:
            self._texts.pop(gone, None)
        return out

    def _observed(self, sources: Optional[Sequence[str]]) -> List[Any]:
        """The records this round is allowed to read.

        The sandbox copies real states in, and the real ones include the
        notes the agent writes to accompany its own scheduled tasks. The
        filter has to be here too or the debugger would be reading a
        different population from the thing it is debugging, which is the
        one thing it must not do.
        """
        records = self._records()
        if not sources:
            return records
        wanted = set(str(one) for one in sources)
        return [record for record in records
                # A record whose source was not written down is read: it
                # came from a file a person may have written by hand, and
                # dropping it silently is worse than including it.
                if str((record.metadata or {}).get("state_source")
                       or record.source or "") in wanted
                or not (record.metadata or {}).get("state_source")]

    def since(self, moment: datetime, limit: int = 1000,
              sources: Optional[Sequence[str]] = None,
              users_only: bool = False) -> List[Any]:
        """The states touched since a moment, newest first. Made-up states
        are all the user's, so `users_only` changes nothing here.

        The same contract as the real store's `since`, including that the
        boundary is inside it.
        """
        out = []
        for record in self._observed(sources):
            # WHEN IT IS ABOUT, which for a real state is when it was
            # written; a made-up one is written now about then.
            when = record.created_at or record.updated_at
            if when is None:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < moment:
                continue
            out.append((when, _made_up(record)))
        out.sort(key=lambda row: row[0], reverse=True)
        return [state for _when, state in out[:max(0, int(limit))]]

    def recent(self, limit: int = 50,
               sources: Optional[Sequence[str]] = None,
               users_only: bool = False) -> List[Any]:
        """The newest states in the folder, newest first.

        The same contract as the real store's `recent`, because the round
        calls one or the other and must not know which.
        """
        out = []
        for record in self._observed(sources):
            # WHEN IT IS ABOUT, which for a real state is when it was
            # written; a made-up one is written now about then.
            when = record.created_at or record.updated_at
            if when is None:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            out.append((when, _made_up(record)))
        out.sort(key=lambda row: row[0], reverse=True)
        return [state for _when, state in out[:max(0, int(limit))]]

    def after(self, moment: datetime, limit: int = 1000,
              sources: Optional[Sequence[str]] = None,
              inclusive: bool = False) -> List[Any]:
        """The states touched after a moment, oldest first - the real
        store's contract."""
        out = []
        for record in self._observed(sources):
            when = record.updated_at or record.created_at
            if when is None:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < moment or (when == moment and not inclusive):
                continue
            out.append((when, _made_up(record)))
        out.sort(key=lambda row: row[0])
        return [state for _when, state in out[:max(0, int(limit))]]

    def by_ids(self, state_ids: Sequence[str]) -> List[Any]:
        wanted = {str(one) for one in state_ids}
        if not wanted:
            return []
        made = (_made_up(record) for record in self._observed(None))
        return [state for state in made if state.id in wanted]

    def counts(self, sources: Optional[Sequence[str]] = None
               ) -> Dict[str, int]:
        return {"active": len(self._observed(sources))}

    def timeline(self, sources: Optional[Sequence[str]] = None
                 ) -> List[str]:
        """When every state in the folder was written, oldest first.

        Read from the files themselves. The folder IS the record here, so
        the overview drawn from this is drawn from what a person can open
        and count - which is the only way a picture of one's own data is
        worth trusting.
        """
        when = []
        for record in self._observed(sources):
            moment = record.observed_from or record.created_at
            if moment is not None:
                when.append(moment.isoformat())
        return sorted(when)


def _how_much(found: str) -> str:
    """What a search returned, in a few words.

    Read off the answer rather than counted separately, so the sentence
    cannot disagree with the thing it is describing.
    """
    said = str(found or "")
    if not said.strip():
        return "nothing at all"
    if "asked for nothing" in said:
        return "nothing, because the request named neither words nor ids"
    hit = re.search(r"for [^:]+: (\d+) match", said)
    if hit:
        count = int(hit.group(1))
        if not count:
            return ("no state in this group carries any of those words - "
                    "the search ran and came back empty")
        shown = said.count("\n--- ")
        return (f"{count} state(s) carry them"
                + (f", of which {shown} are set out below" if shown else ""))
    shown = said.count("\n--- ")
    return f"{shown} state(s), set out below" if shown else "what follows"


def _as_list(value: Any) -> List[str]:
    """Something that was a list before it went through a file.

    A string is NOT iterated: that is the whole point. A list written
    into a record's front matter comes back as its own repr, and a caller
    that takes it for a list gets one item per character - forty lines
    each holding one letter of a path, which is what this looked like.
    """
    if isinstance(value, (list, tuple)):
        return [str(one) for one in value]
    said = str(value or "").strip()
    if not said:
        return []
    try:
        held = json.loads(said)
    except (TypeError, ValueError):
        return [said]
    if isinstance(held, (list, tuple)):
        return [str(one) for one in held]
    return [str(held)]


def _as_map(value: Any) -> Dict[str, Any]:
    """Something that was a map before it went through a file.

    Written as JSON and read back as JSON, but tolerant of both: a record
    written by an older build holds the map's repr, and a caller that
    never went near a file holds the map itself. Neither should raise in
    the middle of a round over something as small as a weekday.
    """
    if isinstance(value, dict):
        return dict(value)
    said = str(value or "").strip()
    if not said:
        return {}
    try:
        held = json.loads(said)
    except (TypeError, ValueError):
        return {}
    return dict(held) if isinstance(held, dict) else {}


def _of(state: Any, name: str) -> Any:
    """A field of a state, whether it is on the object or in its metadata.

    A real `State` keeps its evidence, its sources and its reasoning in
    `metadata`; other things that stand in for one keep them as
    attributes. The copier should not have to know which it was handed.
    """
    held = getattr(state, "metadata", None) or {}
    if isinstance(held, dict) and held.get(name) is not None:
        return held[name]
    return getattr(state, name, None)


def _local_for(starts_at: Any, ends_at: Any) -> Dict[str, Any]:
    """The local clock for a made-up span, in this machine's zone."""
    try:
        from ...agent.states.store import local_span

        return local_span(starts_at, ends_at)
    except Exception:                # pragma: no cover - a label, not a step
        log.debug("could not work out a local time for the sandbox",
                  exc_info=True)
        return {}


class _MadeUp:
    """One state read back out of a file in the sandbox."""

    def __init__(self, record: Any) -> None:
        self.id = record.id
        self.title = record.title
        # The detail is the summary line the record was written with; the
        # body carries the same words plus the file's own furniture, which
        # is not part of what the state said.
        self.detail = record.summary or ""
        # Out of the record rather than hardcoded empty. These were two
        # empty lists and nothing else, so every state in the sandbox
        # rested on no file, recorded no evidence, had no reasoning and
        # no local clock - which made the debugger a test of a thinner
        # state than the one the real round reads.
        held = dict(getattr(record, "metadata", None) or {})
        self.metadata: Dict[str, Any] = {
            "evidence": _as_list(held.get("evidence")),
            "sources": _as_list(held.get("sources")),
            "reasoning": str(held.get("reasoning") or ""),
            "local": _as_map(held.get("local")),
        }
        self.starts_at = record.observed_from
        self.ends_at = record.observed_to
        self.created_at = record.created_at
        self.updated_at = record.updated_at or record.created_at
        self.status = "active"


def _made_up(record: Any) -> _MadeUp:
    return _MadeUp(record)


class _Node:
    """One state, reduced to what the grouping reads.

    The words are taken twice: what the state says, and what it says it
    rests on. Kept apart all the way through - through the scoring, into
    the report - because "these two describe the same work" and "these two
    touch the same file" are different findings and the reader wants to
    know which one grouped a pair.
    """

    def __init__(self, state: Any) -> None:
        self.id = str(getattr(state, "id", "") or "")
        self.title = str(getattr(state, "title", "") or "")
        self.detail = str(getattr(state, "detail", "") or "")
        metadata = getattr(state, "metadata", None) or {}
        self.evidence = [str(item) for item in (metadata.get("evidence") or [])]
        self.sources = [str(item) for item in (metadata.get("sources") or [])]
        self.reasoning = str(metadata.get("reasoning") or "")
        #: The span in the user's own week: the offset that was in force,
        #: the local clock times, and which day it was. UTC is what these
        #: are stored in and is not a week anybody recognises.
        self.local = dict(metadata.get("local") or {})
        #: What was open: the files, pages and documents this state names.
        #: Taken from the state where the review recorded it, and worked
        #: out from what it wrote where it did not.
        held = metadata.get("workspace")
        self.workspace = dict(held) if isinstance(held, dict) else _workspace(
            [str(one) for one in (metadata.get("sources") or [])],
            [str(one) for one in (metadata.get("evidence") or [])])
        self.starts_at = getattr(state, "starts_at", None)
        self.ends_at = getattr(state, "ends_at", None)
        self.created_at = getattr(state, "created_at", None)
        # When it was last written, which is what "new since the last
        # round" is measured by.
        self.updated_at = getattr(state, "updated_at", None) or self.created_at
        self.status = str(getattr(state, "status", "") or "")
        #: The part of a day that is no piece of work, as the review that
        #: wrote it said: never grouped into a task.
        self.routine = str(metadata.get("kind") or "").lower() == "routine"
        #: Done for enjoyment (True), towards something (False), or not said
        #: - a state written before it was asked (None). Grouped only with
        #: its own kind.
        said = str(metadata.get("kind") or "").lower()
        self.leisure = True if said == "leisure" else False if said == "work" else None
        self.said = _terms(" ".join([self.title, self.detail]))
        self.rests_on = _terms(" ".join(self.evidence + self.sources))

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "title": self.title, "detail": self.detail,
                "evidence": self.evidence, "sources": self.sources,
                # Why the review wrote the state. Read off it in __init__
                # and then never passed on, so the search that promised
                # the whole state was handing back the same four fields
                # the sample already had.
                "reasoning": self.reasoning,
                "status": self.status,
                "starts_at": _moment(self.starts_at),
                "ends_at": _moment(self.ends_at),
                "local": dict(self.local),
                "workspace": dict(self.workspace),
                "added": _moment(self.created_at),
                "said_terms": sorted(self.said)[:20],
                "rests_on_terms": sorted(self.rests_on)[:20]}


def _terms(text: str) -> Set[str]:
    """The words of a piece of text, as a set, without the filler.

    The same splitter the library's index uses, so that "what these two
    states have in common" is measured in the same units as "what a search
    for this would match" - two notions of similarity over one folder,
    disagreeing, would be worse than one rough one.

    And each word broken at its punctuation, the way the index breaks a
    keyword into pieces. The splitter keeps the full stop on the last word
    of a sentence, so "replication package." and "replication package, the
    figures" share one term out of two - which is a score about where the
    sentences ended rather than about what they said. A path is the same
    case: `scripts/census_api/fetch.py` and `figures/fetch.py` are the same
    file being worked on.
    """
    found: Set[str] = set()
    for term in search_terms(text, limit=TERMS_PER_SIDE):
        for piece in re.split(r"[^0-9A-Za-z一-鿿]+", term):
            if len(piece) > 1 and piece not in COMMON_WORDS:
                found.add(piece)
    return found


def similarity(left: Set[str], right: Set[str]) -> float:
    """How much two sets of words overlap, 0 to 1.

    The overlap over the smaller set rather than over the union, softened
    by the size difference. A state whose detail runs to a paragraph and
    one whose detail is a line can be about exactly the same thing, and
    Jaccard scores that pair low for a reason that has nothing to do with
    the question: the union is mostly the longer one's words.

    So: what share of the smaller one is in the bigger one, multiplied by
    the square root of how alike their sizes are. Two words in common out
    of two is not the same evidence as sixty out of sixty, and the second
    factor is what keeps the first from saying it is.
    """
    if not left or not right:
        return 0.0
    shared = len(left & right)
    if not shared:
        return 0.0
    smaller, bigger = sorted((len(left), len(right)))
    return (shared / smaller) * math.sqrt(smaller / bigger)


def _cut_into(count: int, merges: Sequence[Any], want: int) -> List[List[int]]:
    """The tree as `want` pieces, split at its tallest joins.

    Not `layers`: that answers "what would a reader call a task", with
    tests for standing out and being worth opening. This answers "give me
    forty pieces, whatever they are", because every state has to end up
    somewhere - a state in no piece is a state the model never sees.
    """
    children = {count + index: (merge.left, merge.right)
                for index, merge in enumerate(merges)}
    heights = {count + index: merge.height
               for index, merge in enumerate(merges)}

    def leaves(node: int) -> List[int]:
        if node not in children:
            return [node]
        out: List[int] = []
        stack = [node]
        while stack:
            here = stack.pop()
            if here in children:
                stack.extend(children[here])
            else:
                out.append(here)
        return out

    pieces = list(_roots(count, merges))
    while len(pieces) < want:
        # The piece whose own join is tallest is the one the tree is most
        # sure divides in two.
        splittable = [node for node in pieces if node in children]
        if not splittable:
            break
        tallest = max(splittable, key=lambda node: heights.get(node, 0.0))
        pieces.remove(tallest)
        pieces.extend(children[tallest])
    return [leaves(node) for node in pieces]


#: What a state id looks like, so that one can be kept out of what the
#: user reads: a short word, an underscore, and hex.
_AN_ID = re.compile(r"\b(?:rec|state|task|group)_[0-9a-f]{6,}\b",
                    re.IGNORECASE)


def _ids_in(text: str) -> List[str]:
    return _AN_ID.findall(str(text or ""))


#: The words a sentence uses to join one thing to another.
#:
#: Counted rather than forbidden in code, because a title CAN honestly need
#: one - "the visa and expense paperwork" is one errand with two names. Two
#: or more is the shape the prompt is asking against: the group described
#: from too low down, as the list of its states with the dates taken off.
_JOINS = (" and ", " then ", ", then ", " as well as ", "; ", " plus ",
          " followed by ", " after which ")


def _is_a_list(text: str) -> int:
    """How many times this sentence joins one thing to another.

    Only the first sentence is asked about wherever this is used on a
    summary: the sentences after it are the particulars and the edges, and
    those are meant to have clauses in them. It is the claim itself -
    the imperative at the front - that has to be one thing.
    """
    first = str(text or "").strip()
    stop = min([n for n in (first.find(". "), first.find(".\n"))
                if n > 0] or [len(first)])
    first = first[:stop].lower()
    joins = sum(first.count(word) for word in _JOINS)
    # A comma in a list of three or more ("a, b and c") is the same shape.
    if joins and first.count(",") > 1:
        joins += 1
    return joins


#: WHICH LANGUAGE THE SUMMARY IS WRITTEN IN: every field the user reads in
#: theirs - a summary in their language with an English title on it reads as
#: a mistake - and what it searches the states with in theirs, English.
SUMMARY_LANGUAGE = in_display(
    "every field the user reads - the title, the summary, the four parts, the reason the "
    "group holds together, the evidence, and the sentence about whether it is concrete"
) + in_english('the keywords you search the states with ("need") - the states are '
               "written in English, and a word is found only as it is written; and the "
               '"in_english" copy, when it is asked for')
#: And a task's status.
STATUS_LANGUAGE = in_display("the status, and why") + in_english(
    '"status_in_english", when it is asked for')


def _the_language(config: Any) -> str:
    """The language the user reads the application in, by name.

    Its own function because two prompts need it inside their body, and
    because a failure here should cost the sentence rather than the
    round: a language that cannot be resolved falls back to naming none,
    which leaves the instruction at the foot to say it alone.
    """
    try:
        from ...common.languages import display_language, name_of

        return str(name_of(display_language(config)) or "the user's language")
    except Exception:                # pragma: no cover - a label, not a step
        log.debug("could not name the display language", exc_info=True)
        return "the user's language"


def _already_known(described: Dict[str, Any]) -> str:
    """What this task was last described as, for the model to amend.

    Put after the states rather than before them, so the states are read
    first: a description shown first is a conclusion, and what follows it
    gets read as evidence for it.
    """
    lines = ["ALREADY KNOWN. This task has been described before, and this is",
             "that description. The states above include ones it has since",
             "gained. Send back ONLY the parts that are now wrong - anything",
             "you leave out is kept exactly as it reads here.",
             "",
             "Anything you do send back is written the same way these are:",
             "complete sentences whose subject is 'the user', saying what",
             "happened and why, not fragments.",
             ""]
    # THE NAME AND THE ONE-LINE SUMMARY ARE PART OF WHAT IS KNOWN.
    #
    # They were left off this list while the line above promised that
    # anything left out is kept. So a model told "send only what changed"
    # left the title out - correctly, as far as it could tell - and the
    # answer was thrown away for having no title. Three tasks went a day
    # without a name that way, each answered in full every round: 3,445
    # characters, eleven fields, and not the two the reader needed.
    #
    # When a task has never had a name, it says so in words the model
    # cannot read as "unchanged".
    for part in ("title", "summary"):
        said = str(described.get(part) or "").strip()
        lines.append(f"{part}: " + (said or
                     "(NONE YET - you must send one; leaving it out "
                     "leaves this task without a name)"))
    for part in DESCRIBED_IN:
        said = str(described.get(part) or "").strip()
        empty = "(not said yet - fill it in if the states now support it)"
        lines.append(f"{part}: {said or empty}")
    lines.append("")
    return "\n".join(lines)


def _amended(answered: Dict[str, Any], before: Any) -> Dict[str, Any]:
    """An answer to an ALREADY KNOWN prompt, with what it left out kept.

    The prompt says "anything you leave out is kept exactly as it reads
    here", so an answer is an amendment: what it sends replaces, what it
    omits stands. Without this the name was read from the answer alone,
    and an amendment that did not touch the name was thrown away whole -
    its new status and new weights with it - for having no title.
    """
    out = dict(answered or {})
    held = dict(before or {})
    for part in ("title", "summary") + tuple(DESCRIBED_IN):
        if not str(out.get(part) or "").strip() and str(
                held.get(part) or "").strip():
            out[part] = held[part]
    # AND THE JUDGEMENTS. An answer that sent only `status` left out
    # whether the group is a task and how much it matters, and those were
    # then read as never answered: "concrete" became true - which showed a
    # group the model had turned down, beside its own words saying it was
    # a state with nothing to finish - and both weights fell to 4.
    for part in ("concrete", "concrete_why", "urgency", "importance",
                 "weighed_why"):
        if out.get(part) in (None, "") and held.get(part) not in (None, ""):
            out[part] = held[part]
    # And the English copy: what it sends replaces, what it omits stands.
    if isinstance(held.get("in_english"), dict) or isinstance(out.get("in_english"), dict):
        out["in_english"] = dict(held.get("in_english") or {},
                                 **(out.get("in_english") if isinstance(
                                     out.get("in_english"), dict) else {}))
    return out


def _a_number_between(said: Any, low: int, high: int) -> Optional[int]:
    """One of the numbers on a scale, or None if it is not one.

    A model asked for 1 to 7 can answer "6", 6, 6.0, "6/7" or "high". The
    first four are the number; the last is not, and guessing a number for
    it would invent a reading the model did not give. None means the
    caller keeps whatever it had, which for a weight is better than a
    confident 4 standing in for an answer that never came.
    """
    if said is None or isinstance(said, bool):
        return None
    try:
        number = int(round(float(str(said).strip().split("/")[0])))
    except (TypeError, ValueError):
        return None
    return max(low, min(high, number))


#: What a task says while it is waiting for its turn. A round is minutes
#: of work on a busy folder, and the alternative to this is showing last
#: round's words as though they were this round's - a reader cannot tell
#: a name that is six hours old from one just written, and the whole
#: point of a round is that something changed.
WAITING = "preparing"

#: What the marker used to be. A folder written by an earlier build has
#: this word stored as a title, and it is not a name either - forgetting
#: that would leave those tasks called "loading" for ever, since a task
#: that looks named is never asked about again.
WAITING_BEFORE = ("loading",)


def _writer(response: Any) -> Dict[str, str]:
    """Which model wrote an answer: its name and provider, and when."""
    return {"model": str(getattr(response, "model", "") or ""),
            "provider": str(getattr(response, "provider", "") or ""),
            "at": task_now()}


def _writer_said(writer: Any) -> str:
    """The same, as one line: "claude-opus-5-5 (anthropic)"."""
    writer = writer if isinstance(writer, dict) else {}
    model = str(writer.get("model") or "").strip()
    provider = str(writer.get("provider") or "").strip()
    if not model:
        return ""
    return f"{model} ({provider})" if provider else model


def _numbered_in(said: Any, shown: Sequence[str]) -> List[str]:
    """The ids of the shown states named by NUMBER (1 for the first) - a
    number off the list dropped, and nothing when more than half are named:
    an answer that says most of a task is not it is no answer to act on."""
    if not isinstance(said, list):
        return []
    numbers = []
    for value in said:
        try:
            numbers.append(int(str(value).strip().lstrip("#").split()[-1]))
        except (TypeError, ValueError, IndexError):
            continue
    picked = [shown[n - 1] for n in dict.fromkeys(numbers)
              if 1 <= n <= len(shown) and shown[n - 1]]
    present = [one for one in shown if one]
    if not picked or len(picked) * 2 > len(present):
        return []
    return picked


def _buried_in(said: Any, group: Dict[str, Any]) -> Dict[str, Any]:
    """The task the model named inside a group it turned down, by the ids
    of the shown states it is about - or nothing.

    The model was shown the states numbered, so it answers in numbers;
    a number off the list is dropped, and naming every shown state is
    the group itself, which is not a task taken out of it."""
    if not isinstance(said, dict):
        return {}
    shown = [str((one.get("member") or {}).get("id") or "")
             for one in group.get("sample") or []]
    numbers = []
    for value in said.get("states") or []:
        try:
            numbers.append(int(str(value).strip().lstrip("#")))
        except (TypeError, ValueError):
            continue
    picked = [shown[n - 1] for n in dict.fromkeys(numbers)
              if 1 <= n <= len(shown) and shown[n - 1]]
    present = [one for one in shown if one]
    if not picked or len(picked) >= len(present):
        return {}
    return {"title": _without_ids(str(said.get("title") or "").strip())[:300],
            "states": picked, "shown": present}


#: How many earlier versions a task keeps.
HISTORY_KEPT = 30


def _version_of(task: Any, replaced_by: str) -> Optional[Dict[str, Any]]:
    """The description a task had, as an entry in its history - or None if
    it had none, or this is the same one again."""
    if not task.summary_id or task.summary_id == replaced_by:
        return None
    held = task.described or {}
    was = held.get("was") or {}
    title = str(held.get("title") or was.get("title") or "")
    if not title:
        return None
    changed = task.changed_since_named()
    drift = task.drift()
    if changed >= 0.01:
        why = f"{round(changed * 100)}% of its states changed since it was named"
    elif drift:
        why = f"its work moved (drift {drift:.2f})"
    else:
        why = "described again"
    return {"at": task.summary_at, "record_id": task.summary_id,
            "title": title, "summary": str(held.get("summary") or "")[:600],
            "size": task.summary_members, "why": why}


def _a_real_name(described) -> str:
    """The stored name, unless what is stored is the marker.

    THE WORD IS NEVER A NAME. A task queued for rewriting has `loading`
    written into its description and a flag beside it saying so - but the
    flag was added later than the word, and a database written by the
    build before it holds the word with no flag at all. Measured on the
    live agent: nine tasks, four of them carrying `loading` as their title
    with nothing to say it was a marker, so the picture drew the word as
    the name of the work and no later round would ever replace it.

    Checked on the word itself, because that is the thing that is
    actually wrong to show.
    """
    held = dict(described or {})
    name = str(held.get("title") or "")
    # The flag no longer hides the name: a task can be waiting for a new
    # description and still have the one it was given last time, which is
    # the whole point of not overwriting it.
    return "" if name == WAITING or name in WAITING_BEFORE else name


def _is_waiting(described) -> bool:
    return bool((described or {}).get("waiting"))


def _waiting_on(described, size: int):
    """Note that a description is owed. The words it has are untouched.

    THE PLACEHOLDER IS NOT A VALUE. This used to write the word into the
    task's own title and move the real name aside for safekeeping, which
    is three mistakes in one: the stored description became a lie, every
    reader had to know the word was not a name, and marking a task twice
    - which a resumed round does - saved the word AS the name it used to
    have and the real one was gone for good. Found in the live folder:
    four tasks whose title and whose "previous title" were both the
    placeholder, with nothing left to put back.

    What is actually true is "a description is owed", so that is what is
    recorded. The name stays where it is until a better one replaces it,
    and the page draws a placeholder for a task that has NO name - which
    it can work out for itself, because an absent name is absent.
    """
    held = dict(described or {})
    held["waiting"] = True
    held["waiting_size"] = int(size)
    # Anything an older build moved aside is brought back, since there is
    # no longer anywhere for it to be except in the title.
    was = dict(held.pop("was", None) or {})
    for name in ("title", "summary", "status"):
        if held.get(name) in (WAITING, *WAITING_BEFORE):
            held.pop(name, None)
        if not held.get(name) and was.get(name) not in (
                None, WAITING, *WAITING_BEFORE):
            held[name] = was[name]
    return held


def _up_to(nodes, moment: str):
    """The states at or before a moment, keeping the order they came in.

    A state with no start time is KEPT rather than dropped: the line is
    there to stop a round reading its own tail, and a state that cannot
    say when it happened is not evidence of that. Dropping it would be a
    grouping quietly missing work because of a missing field.
    """
    if not moment:
        return list(nodes)
    try:
        edge = datetime.fromisoformat(moment)
    except ValueError:
        return list(nodes)
    if edge.tzinfo is None:
        edge = edge.replace(tzinfo=timezone.utc)
    kept = []
    for node in nodes:
        said = getattr(node, "starts_at", "") or ""
        if not said:
            kept.append(node)
            continue
        try:
            when = datetime.fromisoformat(str(said))
        except ValueError:
            kept.append(node)
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when <= edge:
            kept.append(node)
    return kept


def _without_ids(text: str) -> str:
    """The same words with any state id taken out, tidily.

    Taken out rather than replaced with a placeholder: "see [id]" is no
    more use to a reader than the id was, and a sentence that only made
    sense with the id in it was a sentence about the wrong thing.
    """
    out = _AN_ID.sub("", str(text or ""))
    out = re.sub(r"\[\s*\]|\(\s*\)", "", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    return out.strip(" ,;:-")


def _when_it_ran(member: Dict[str, Any]) -> str:
    """When a state ran, in the week the user actually had.

    The stored times are UTC, which is right for arithmetic and is not a
    week anybody recognises: a state from 23:00 to 01:00 is a Tuesday
    evening in Lisbon and a Wednesday morning in Shanghai, and a model
    asked whether somebody works late has no way to choose. So the local
    clock is what is shown, with the day named, and the zone said once so
    the reading can be checked. Where a state has no local time - written
    before they carried one, and the backfill could find no table for the
    zone - the UTC is given AND LABELLED as UTC, rather than quietly
    presented as though it were the user's own clock.
    """
    said = dict(member.get("local") or {})
    began = str(said.get("starts_at") or "")
    ended = str(said.get("ends_at") or "")
    if not began:
        return (f"ran: {member.get('starts_at') or '?'} to "
                f"{member.get('ends_at') or '?'} (UTC - this state records "
                f"no local time)")
    day = str(said.get("starts_at_day") or "")
    zone = str(said.get("zone") or "")
    ending = str(said.get("ends_at_day") or "")
    line = f"ran: {day}, {began}"
    line += f" to {ended}" if ended else " to ?"
    if ending and ending != day:
        line += f" ({ending})"
    if zone:
        line += f" [{zone}]"
    if said.get("assumed"):
        # An assumed zone and an observed one are different claims, and a
        # weekday derived from a guess should say that it was.
        line += " - zone assumed, not recorded at the time"
    return line


def _whole_state(member: Dict[str, Any], heading: str) -> List[str]:
    """One state with nothing held back, and every field named.

    THE SAMPLE IS DELIBERATELY THINNER THAN THIS: a title, a detail, when
    it ran and eight of each kind of evidence, because forty of those is
    what fits. This is what a state looks like when the model has asked
    for it - the reasoning the review recorded, its status, and ALL of its
    evidence and sources rather than the first eight.

    An empty field is SAID to be empty rather than left out. "This state
    records no evidence" is an answer; a missing line is indistinguishable
    from a state whose evidence nobody passed on, and the model cannot
    tell that it has been given less than it asked for.
    """
    lines = [f"--- {heading} ---", f"title: {member.get('title') or '(none)'}"]
    lines.append(f"detail: {member.get('detail') or '(no detail)'}")
    lines.append(f"why the review wrote it: "
                 f"{member.get('reasoning') or '(it did not say)'}")
    lines.append(_when_it_ran(member))
    lines.append(f"status: {member.get('status') or '(none)'}")
    sources = list(member.get("sources") or [])
    if sources:
        for item in sources:
            lines.append(f"  rests on: {item}")
    else:
        lines.append("  rests on: (this state names no file, page or window)")
    evidence = list(member.get("evidence") or [])
    if evidence:
        for item in evidence:
            lines.append(f"  evidence: {item}")
    else:
        lines.append("  evidence: (this state records none)")
    lines.append("")
    return lines


def _unit_vector(values: Sequence[float]) -> List[float]:
    """The same middle a task uses, so the two measure the same thing."""
    length = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / length for value in values]


def _stamp(when: str) -> str:
    """A moment, as something that can sit inside an id."""
    return "".join(ch for ch in when[:19] if ch.isdigit())


def _renumbered(task: Dict[str, Any], name: str) -> Dict[str, Any]:
    """The same task under a new name, and its children under theirs."""
    return {**task, "id": name, "depth": max(0, task.get("depth", 1) - 1),
            "children": [_renumbered(child, f"{name}.{index + 1}")
                         for index, child in enumerate(task.get("children") or [])]}


def _either(now: Any, before: Any) -> Any:
    """This round's answer, or the one the task already had."""
    return before if now is None else now


def _weighed_as(described: Any, was: Dict[str, Any]) -> Dict[str, Any]:
    """A description with the weights it has, from wherever they are."""
    held = dict(described or {})
    for name in ("urgency", "importance", "weighed_how", "weighed_why"):
        if held.get(name) is None and was.get(name) is not None:
            held[name] = was[name]
    return held


def _every_piece(pieces: Sequence[Dict[str, Any]]):
    """Each piece and everything inside it, however deep it goes."""
    for piece in pieces or []:
        yield piece
        for deeper in _every_piece(piece.get("children") or []):
            yield deeper


def _a_piece_as_drawn(row: Dict[str, Any]) -> Dict[str, Any]:
    """A kept piece, back in the shape a round builds them in.

    Kept flat - title and summary beside each other - because that is
    what the page reads. A round reads a `summary` holding both, so the
    shape is turned back, and so is everything inside it.
    """
    return {
        "id": row.get("id"),
        "states": list(row.get("states") or []),
        "why_closed": str(row.get("why_closed") or ""),
        "summary": ({"title": row.get("title", ""),
                     "summary": row.get("summary", ""),
                     "concrete": bool(row.get("concrete", True)),
                     "described": dict(row.get("described") or {})}
                    if str(row.get("title") or "").strip() else {}),
        "children": [_a_piece_as_drawn(kid)
                     for kid in (row.get("children") or [])],
    }


def _marked(node: Dict[str, Any], hidden: set,
            leisure: Optional[set] = None) -> Dict[str, Any]:
    """A drawn row, saying whether a summary thought it worth drawing.

    The judgement is on the stored task, not on the round's copy of it,
    because a round that reused a summary does not carry the flag
    forward - which is how a filter written against its copy came to
    match nothing on that path while working on the other.
    """
    out = dict(node)
    which = str(out.get("task_id") or out.get("id") or "")
    out["hidden"] = which in hidden
    # A task of LEISURE: the page's "work activity" leaves it out.
    out["leisure"] = which in (leisure or set()) or bool(out.get("leisure"))
    return out


def _with_last_name(node: Dict[str, Any],
                    known: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """A drawn row, with last round's name where this one has none yet."""
    if node.get("title"):
        return node
    was = known.get(str(node.get("task_id") or ""), {})
    if not was.get("title"):
        return node
    out = dict(node)
    out["title"] = was["title"]
    # Still waiting, and the name is not this round's - the row says so
    # rather than passing off an old answer as a new one.
    out["waiting"] = True
    out["name_is_old"] = True
    return out


def _working_now(which: Any, report: Dict[str, Any]) -> bool:
    """Whether this node, or something in it, is being summarised - by any
    of the calls going at once."""
    return any(_is_working(which, busy) for busy in
               [report.get("summarising"), *(report.get("summarising_all") or [])])


def _is_working(which: Any, busy: Any) -> bool:
    """Whether this node is the one being summarised, or holds it.

    Ids are built by appending to the parent's - `task_3`, `task_3.1`,
    `task_3.1.2` - so "holds it" is "is a prefix of it, at a dot". The
    dot matters: without it `task_3` would also claim `task_31`.
    """
    if not which or not busy:
        return False
    which, busy = str(which), str(busy)
    return busy == which or busy.startswith(f"{which}.")


def _how_many_inside(node: Dict[str, Any]) -> Dict[str, int]:
    """A node's immediate sub-tasks, and all of them down to the bottom.

    Both numbers, because they answer different questions and are
    different for any task worth opening: "4 sub-tasks" is what the first
    click shows, and "9 in all" is how much work is under the task
    altogether. One number alone makes the other a surprise.
    """
    straight = list(node.get("children") or [])
    return {"children": len(straight),
            "inside": _count_tasks(straight)}


def _count_tasks(tasks: Sequence[Dict[str, Any]]) -> int:
    """How many tasks the whole tree holds, at every depth."""
    return sum(1 + _count_tasks(task.get("children") or []) for task in tasks)


def _deepest(tasks: Sequence[Dict[str, Any]], depth: int = 1) -> int:
    """How many layers deep it goes."""
    return max([depth] + [_deepest(task.get("children") or [], depth + 1)
                          for task in tasks if task.get("children")])


#: The decoration a program animates at the front of a window title.
_SPINNER_AHEAD = re.compile(r"^[^\w　-ヿ一-鿿/.:]+")


#: Sites where the part after the host names a piece of work rather than a
#: page: a repository, a document, a project.
_WORK_ON_A_SITE = {"github.com": 2, "gitlab.com": 2, "docs.google.com": 3,
                   "overleaf.com": 2, "www.overleaf.com": 2,
                   "openreview.net": 1}


def _project_root(key: str, folders: Dict[str, str]) -> str:
    """The folder a project lives in, from any path inside it - the deepest
    folder this round knows by name that the path runs through - or, for a
    site, the part of the address that names the work."""
    if not key:
        return ""
    if _is_a_path(key):
        parts = key.split("/")
        known = set(folders.values())
        for depth in range(len(parts), 3, -1):
            prefix = "/".join(parts[:depth])
            if prefix in known:
                return prefix
        # A file: its folder. A folder: itself.
        return "/".join(parts[:-1]) if "." in parts[-1] else key
    host, _, rest = key.partition("/")
    keep = _WORK_ON_A_SITE.get(host)
    if keep:
        bits = [one for one in rest.split("/") if one][:keep]
        return "/".join([host] + bits) if bits else ""
    # A general-purpose site is where things happen, not a piece of work.
    return ""


#: A folder's name as it is written in a sentence.
_FOLDER_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]{6,}[a-z0-9]")


def _is_a_path(key: str) -> bool:
    return bool(re.match(r"^[a-z]:/", key)) or key.startswith("/")


def _canonical_place(key: str) -> str:
    """One spelling for one place: lower case, forward slashes, and the
    hyphen and underscore the review writes interchangeably in a folder's
    name taken as one."""
    text = str(key or "").strip().replace("\\", "/").lower()
    # A WSL path is the Windows drive's: /mnt/c/users/me is c:/users/me.
    if text.startswith("/mnt/") and len(text) > 6 and (
            len(text) == 6 or text[6] == "/"):
        text = f"{text[5]}:{text[6:]}"
    if "/" in text:
        text = text.replace("-", "_").rstrip("/")
    return text


#: Folders under a home folder that are where everything is, not a place.
_EVERYWHERE = ("desktop", "documents", "downloads", "onedrive", "dropbox",
               "appdata", "appdata/local", "appdata/roaming")


def _not_a_place(key: str, own: str = "") -> bool:
    """A place that says nothing about which work it was.

    The agent's own folder, which states about anything mention because
    the agent writes there; temporary folders; and the folders everything
    is under - a drive, the users folder, a home folder and its desktop.
    """
    if not key:
        return True
    if re.fullmatch(r"[a-z]:", key):         # a drive on its own
        return True
    if own and (key == own or key.startswith(own + "/")):
        return True
    if "/appdata/local/temp" in key or key.startswith("/tmp"):
        return True
    if _is_a_path(key):
        parts = key.split("/")
        if len(parts) <= 2:
            return True                     # c:/users, c:/work
        if parts[1] == "users" and (len(parts) <= 3 or "/".join(
                parts[3:]) in _EVERYWHERE):
            return True                     # c:/users/me, .../desktop
    return False


#: What counts as a place, as the grouping reads it - raised whenever that
#: changes, because it changes what a task is: the tasks are found again,
#: once, with the new reading (`_shape`).
PLACES_READ_AS = 2
#: A site with this many different pages in what one round reads is where
#: the user goes for everything - a search engine, a video site, the mail -
#: and not where any one piece of work is.
PAGES_OF_A_SITE_FOR_EVERYTHING = 40
_A_DOCUMENT = re.compile(
    r"\.(pptx?|docx?|xlsx?|pdf|py|md|txt|ipynb|tex|csv|json)\b")
_UNDER_A_HOME = re.compile(r"^[a-z]:/|^/(home|users|mnt|volumes)(/|$)")


def _site_of(place: str) -> str:
    """The site a page is on, or "" for what is not a page."""
    if "/" not in place:
        return ""
    host = place.split("/", 1)[0]
    return host if "." in host else ""


def _telling(places: Sequence[str], pages_of: Dict[str, Set[str]]) -> List[str]:
    """ONLY THE PLACES THAT SAY WHICH WORK IT WAS.

    A folder or a file on disk; a document by its name; a page two levels
    into a site that is not visited for everything. Not a window's title,
    an application's name, a conversation's title, a site's front page:
    each of those turns up in every kind of work - the mail beside a
    project, the chat beside a talk - and credited as a shared place it
    put a day of one piece of work into another. Measured on a week of
    labelled states, reading only these put an eighth fewer states in
    the wrong task; more were left in no task instead, which is the
    cheaper mistake - a state left out is not said to be other work.
    """
    kept: List[str] = []
    for place in places:
        if _UNDER_A_HOME.match(place) or _A_DOCUMENT.search(place):
            kept.append(place)
            continue
        site = _site_of(place)
        if not site:
            continue
        deep = len([part for part in place.split("/")[1:] if part]) >= 2
        if deep and len(pages_of.get(site, ())) < PAGES_OF_A_SITE_FOR_EVERYTHING:
            kept.append(place)
    return kept


def _utc(value: Any) -> str:
    """A moment as an ISO string in UTC, or "" - so they compare as text."""
    if value is None or value == "":
        return ""
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _cosine_of(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    size = (math.sqrt(sum(a * a for a in left))
            * math.sqrt(sum(b * b for b in right)))
    return 0.0 if size <= 0 else max(-1.0, min(1.0, dot / size))


def _as_moment(text: str) -> Optional[datetime]:
    """A stored mark, back as a moment. Nothing at all if it is unreadable."""
    if not text:
        return None
    try:
        out = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def _moment(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _add_part(held: Dict[str, Any], part: str, began: float, ended: float,
              used: Dict[str, int], seconds: Optional[float] = None) -> None:
    """Add one part's cost to a round's account, in place.

    Each part keeps its own seconds, calls and tokens. The round's seconds
    are the UNION of every part's time windows - parts that ran at once
    count once - and its calls and tokens are the parts' sums.
    """
    parts = held.get("parts")
    if not isinstance(parts, dict):
        parts = {}
        # A round counted before it had parts: what it had is its tasks'.
        if int(held.get("calls") or 0) or float(held.get("seconds") or 0):
            parts["tasks"] = {name: held.get(name, 0) for name in
                              ("calls", "tokens_in", "tokens_out", "seconds")}
            held["base_seconds"] = float(held.get("seconds") or 0)
        held["parts"] = parts
    mine = parts.setdefault(part, {"calls": 0, "tokens_in": 0,
                                   "tokens_out": 0, "seconds": 0.0})
    for name in ("calls", "tokens_in", "tokens_out"):
        mine[name] = int(mine.get(name) or 0) + int(used.get(name) or 0)
    mine["seconds"] = round(float(mine.get("seconds") or 0)
                            + (seconds if seconds is not None
                               else max(0.0, ended - began)), 1)
    spans = sorted([list(one) for one in held.get("spans") or []]
                   + [[float(began), float(ended)]])
    merged: List[List[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    held["spans"] = merged
    held["seconds"] = round(float(held.get("base_seconds") or 0)
                            + sum(end - start for start, end in merged), 1)
    for name in ("calls", "tokens_in", "tokens_out"):
        held[name] = sum(int(one.get(name) or 0) for one in parts.values())


class StateHeartbeat:
    """The second heartbeat: what the states already written add up to.

    It writes one record per group it finds, in the same folder the states
    themselves are mirrored to, one level above them (section 50.2): a
    summary that names the states it was made from, so that a reader who
    doubts it can go and read them.
    """

    def __init__(self, *, config: Any, states: Any, library: Any,
                 db: Any = None, models: Any = None, recorder: Any = None,
                 bus: Any = None, observability: Any = None,
                 tracer: Any = None) -> None:
        self.config = config
        self.db = db
        #: The states that fell outside every task at the last
        #: rebuild, kept so a round that only appends can still say
        #: which states group with nothing. PER SCOPE: one list for
        #: both would let a sandbox round leave made-up states
        #: behind for the next real round to report as the user's.
        self._loose_after_rebuild: Dict[str, List[str]] = {}
        #: Per scope: the states read this round because they are NEW, as
        #: against those read back for the tasks that hold them.
        self._fresh_ids: Dict[str, set] = {}
        #: The sandbox's "processed up to"; the real one is a setting.
        self._marks: Dict[str, str] = {}
        #: This round's places and times per state, and how specific each
        #: place is - worked out once and used by every step after.
        self._places_now: Dict[str, List[str]] = {}
        self._when_now: Dict[str, Tuple[str, str]] = {}
        self._specific_now: Dict[str, float] = {}
        self._evidence_now: Dict[str, Dict[str, Any]] = {}
        #: Which project each task is part of, as of this round.
        self._project_of: Dict[str, str] = {}
        #: Relative paths already looked up on disk, and what they were.
        self._resolved_paths: Dict[str, str] = {}
        #: The last sandbox round, kept apart from the real one so the
        #: panel can show both without either standing in for the other.
        self._sandbox_report: Dict[str, Any] = {}
        #: The sub-tasks found inside each task, kept between rounds. A
        #: task that took no new states has the same ones, and finding
        #: them again is sixty thousand comparisons an hour to redraw
        #: what has not moved. Keyed by what would change them.
        self._inside: Dict[str, Any] = {}
        #: The tasks a candidate out of the pile is held against.
        self._live_for_pile: List[Any] = []
        #: What the states of the round in progress mean. Held here only
        #: because the pass that summarises what is inside a state runs
        #: after the round's own locals have gone, and it needs them to
        #: choose which states to show.
        self._vectors_now: Dict[str, List[float]] = {}
        #: Scopes whose work has been given up on, per scope. Emptying the
        #: sandbox sets its own; the round notices and stops.
        self._abandoned: Dict[str, threading.Event] = {}
        #: One lock per scope: see `run_once`. The real round and the
        #: debugger's share no files, no rows and no reason to wait for
        #: each other.
        self._running_by: Dict[str, Any] = {}
        self.states = states
        self.library = library
        self.models = models
        self.recorder = recorder
        self.bus = bus
        self.observability = observability
        self.tracer = tracer or NullTracer()
        #: What a state means, as a vector (section 34.3, as amended). The
        #: model is loaded on the first round that needs it, never at
        #: startup: the import alone is ten seconds, and an application
        #: that never groups a state should never pay them.
        self.embedder = Embedder(config=config, db=db)
        # The model is held from a round's first visit to its end, and let
        # go then - see `run_once`.
        self._model_holds: Dict[str, Any] = {}
        self._visits_in_flight: Dict[str, int] = {}
        self._model_holds_lock = threading.Lock()
        # The plans already answered, by exactly what was sent - see `_plan`.
        self._plans_answered: Dict[Tuple[str, str], Tuple[str, Dict[str, Any], str]] = {}
        self._plans_lock = threading.Lock()
        # HOW MUCH MAY GO AT ONCE, looked at before each call starts - what
        # the user is doing, the processor, memory, the battery, the
        # provider - so the work done side by side never takes the machine
        # from the person using it (common/room.py).
        try:
            from ...platform_layer import get_platform
            activity = get_platform().activity
        except Exception:            # pragma: no cover - no platform layer
            activity = None
        self.room = Room(config=config, models=models, activity=activity,
                         role="heartbeat")
        # The user's experience - each piece of work as stages, and how they
        # work as patterns and principles - kept up after each round.
        from ..experience.service import Experience
        self.experience = Experience(db=db, config=config, states=states,
                                     library=library, embedder=self.embedder,
                                     models=models)
        self.experience.room = self.room
        # THE ONE POOL everything side by side takes its places in
        # (common/pool.py) - the application's, set when it is made; its own
        # until then.
        from ...common.pool import Pool
        self.pool = Pool(self.room)
        self.experience.pool = self.pool
        # Shown is decided here: a real piece of work, with enough of it.
        self.experience.is_shown = lambda task: (
            (task.described or {}).get("concrete") is not False
            and not self._too_short(task))
        # And what each of its parts costs goes into the round's account.
        self._spend_lock = threading.Lock()
        self.experience.on_spent = self._spend
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval_changed = threading.Event()
        self._running = threading.Lock()
        self._last_report: Dict[str, Any] = {}
        #: The last round's drawing, as it was kept when the round ended -
        #: what the panel draws after a restart (see `_keep_the_drawing`).
        self._kept_drawing: Optional[Dict[str, Any]] = None
        self._progress: Dict[str, Any] = {}
        self._last_run_at: Optional[float] = None
        self._next_run_at: Optional[float] = None

    # -- lifecycle ----------------------------------------------------------
    # -- states somebody made up -------------------------------------------
    #: What a pseudo-state calendar is made of: a few subjects, each with
    #: several ways of being written, and one that walks.
    #:
    #: Worded differently within each subject on purpose. A sandbox whose
    #: states repeat one sentence tests nothing: the grouping would be
    #: right about them for the wrong reason, because matching identical
    #: text is not what it does.
    PRETEND = {
        "one project": [
            "They are rebuilding the SWR replication package.",
            "They are working through the replication scripts again.",
            "They are fixing the census fetch step in the replication code.",
            "They are checking the replication figures against the paper.",
            "They are re-running the replication notebook.",
        ],
        "an evening off": [
            "They are watching videos on YouTube.",
            "They are on YouTube again this evening.",
            "They are watching another long video.",
            "They are back on the video site after dinner.",
            "They are clicking through video pages one after another.",
        ],
        "correspondence": [
            "They are answering email about the grant.",
            "They are working through the inbox.",
            "They are replying to the reviewer's message.",
            "They are clearing out Gmail threads.",
            "They are writing back about the trip.",
        ],
        "the trip": [
            "They are booking a flight for the conference.",
            "They are filling in the expense form.",
            "They are arranging the visa paperwork.",
            "They are booking a hotel.",
            "They are submitting the travel claim.",
        ],
    }

    #: One subject walking from the code to the writing-up, a step at a
    #: time. This is the case the drift signal exists for, and it cannot be
    #: made from two separate subjects: those are a new task, not a
    #: drifting one.
    WALKS = [
        "They are rebuilding the replication package.",
        "They are checking the replication figures against the paper.",
        "They are writing notes on what the figures show.",
        "They are drafting a paragraph about the results.",
        "They are writing up what the replication found.",
        "They are rewriting the results section of the paper.",
        "They are revising the discussion.",
        "They are rewriting the abstract.",
    ]

    def make_up_states(self, how_many: int = 60, days: int = 14,
                       seed: int = 0) -> Dict[str, Any]:
        """Fill the sandbox: real states copied first, invented to top up.

        COPIED FROM THE REAL ONES WHERE THERE ARE ENOUGH. Invented states
        are made of five sentences per subject, and a grouping asked to
        separate five sentences is being asked an easier question than the
        one it has to answer: real states are worded differently every
        time, trail off mid-subject, and sit at distances no generator
        chooses. Testing against invented ones proves the arithmetic and
        almost nothing else.

        The invented ones stay for the case the copies cannot cover -
        a calendar with forty states in it cannot be asked to fill a
        window of a thousand - and the report says how many of each went
        in, because a result over copies and a result over inventions are
        different claims.

        A copy takes the state's words and its span and nothing else: no
        id, no history, and it lands in the sandbox's own folder. Nothing
        is read back into the real library from here.
        """
        import random

        where = self.where(SANDBOX)
        made = random.Random(seed or None)
        wanted = max(1, int(how_many))
        wrote: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        real = []
        try:
            if self.states is not None:
                real = list(self.states.recent(limit=wanted * 2,
                                               sources=OBSERVED))
        except Exception:        # pragma: no cover - a copy, not a record
            log.debug("could not read the real states to copy", exc_info=True)
        made.shuffle(real)
        copied = 0
        for index, state in enumerate(real[:wanted]):
            began = now - timedelta(
                minutes=int(index * days * 24 * 60 / max(1, wanted)))
            title = str(getattr(state, "title", "") or "").strip()
            if not title:
                continue
            record = Record(
                folder=STATES, title=title,
                summary=str(getattr(state, "detail", "") or "")[:500],
                body="copied from a real state for the sandbox",
                keywords=["sandbox", "copied"], source="sandbox",
                created_at=began, observed_from=began,
                # WRITTEN NOW, about then - as a real state is. A round
                # reads what was written since the last one, and a made-up
                # state stamped as written when it happened would always
                # look older than the line and never be read.
                updated_at=datetime.now(timezone.utc),
                observed_to=began + timedelta(minutes=15),
                metadata={"subject": "copied from the real calendar",
                          "sandbox": True, "copied": True,
                          # What it was in the real calendar. The copy is
                          # taken from the observed states only, so this
                          # is always one of those - it is written down so
                          # the sandbox filters on the same field the real
                          # store filters on, rather than on the fact that
                          # its input happened to be clean.
                          "state_source": str(
                              getattr(state, "source", "") or "agent"),
                          # AND THE REST OF THE STATE. A copy that keeps
                          # the title and the detail and drops everything
                          # else is not a copy of a state: what it rests
                          # on, the evidence it recorded and the reasoning
                          # the review wrote it with are three of the
                          # things a summary is made from, and a search
                          # that asks for a state "in full" was handing
                          # back four fields and three apologies. The
                          # sandbox existed to try the real thing on a
                          # copy of the real data, and was trying a
                          # thinner thing on a thinner copy.
                          # As JSON for the same reason as `local`
                          # below: a list in the front matter comes back
                          # as its own repr, and iterating that gives one
                          # character at a time - which showed up as
                          # forty "rests on" lines spelling out a path.
                          "evidence": json.dumps(
                              [str(one) for one in
                               (_of(state, "evidence") or [])][:40]),
                          "sources": json.dumps(
                              [str(one) for one in
                               (_of(state, "sources") or [])][:40]),
                          "reasoning": str(_of(state, "reasoning") or ""),
                          # The local clock too, shifted with the state.
                          # The sandbox moves a state to a new moment so
                          # the calendar it draws is its own, so keeping
                          # the original's wall-clock time would put a
                          # Tuesday evening on a Friday morning.
                          # As JSON, because a record's front matter
                          # carries scalars and lists and turns a nested
                          # map into its own repr on the way back - which
                          # arrives as a string and raises the moment
                          # anything treats it as the map it looks like.
                          "local": json.dumps(_local_for(
                              began, began + timedelta(minutes=15)))},
            )
            written = where.library.write(record)
            wrote.append({"id": written.id, "title": title,
                          "subject": "copied from the real calendar"})
            copied += 1

        step = 0
        for index in range(max(0, wanted - copied)):
            subjects = list(self.PRETEND)
            if index % 4 == 0:
                title = self.WALKS[min(len(self.WALKS) - 1,
                                       step * len(self.WALKS)
                                       // max(1, max(1, wanted - copied) // 4))]
                step += 1
                subject = "the one that walks"
            else:
                subject = made.choice(subjects)
                title = made.choice(self.PRETEND[subject])
            began = now - timedelta(
                minutes=int((copied + index) * days * 24 * 60
                            / max(1, wanted)))
            record = Record(
                folder=STATES,
                title=title,
                summary=made.choice([
                    "A stretch of it, as usual.", "More of the same work.",
                    "Another sitting on it.", "Carrying on from earlier.",
                    "A short spell on it."]),
                body=f"made up for the sandbox\nsubject: {subject}",
                keywords=["sandbox"],
                source="sandbox",
                created_at=began,
                observed_from=began,
                updated_at=datetime.now(timezone.utc),
                observed_to=began + timedelta(minutes=15),
                metadata={"subject": subject, "sandbox": True},
            )
            written = where.library.write(record)
            wrote.append({"id": written.id, "title": title,
                          "subject": subject})
        counted: Dict[str, int] = {}
        for row in wrote:
            counted[row["subject"]] = counted.get(row["subject"], 0) + 1
        return {
            "where": where.to_dict(),
            "written": len(wrote),
            "copied": copied,
            "invented": len(wrote) - copied,
            "over_days": days,
            "subjects": counted,
            "states": wrote[:40],
            "why": (f"{copied} copied from the real calendar and "
                    f"{len(wrote) - copied} invented. The copies are what "
                    f"make this worth running: invented states are made of "
                    f"five sentences per subject, and separating five "
                    f"sentences is an easier question than the one the "
                    f"grouping actually has. Inventions only fill what the "
                    f"copies could not. The subject on each file is not read "
                    f"by the grouping - only the title and the detail are."),
        }

    def run_in_the_sandbox(self, how_many: int = 0, days: int = 14,
                           seed: int = 0, fresh: bool = True
                           ) -> Dict[str, Any]:
        """Empty the sandbox, fill it, and run one round over what is in it.

        A press of a button in a debugger should be a whole thing. This
        one was half of one: it ran the grouping over whatever happened
        to be in the sandbox, so on a sandbox nobody had filled it read
        nothing, found nothing and drew nothing - and said so in a way
        that reads as a fault in the grouping rather than as an empty
        folder.

        `fresh` is how the tests and the make-some-up-then-look-at-it
        path keep their own states: with it false this is what it always
        was, a round over what is there. From the page it is true, and
        every press is a fresh grouping.

        Which means two presses never show appending, and should not: a
        round over states that have not changed has nothing to append.
        Appending is what `rehearse_append` exists to demonstrate, in
        three rounds, and it says so on the page.
        """
        out: Dict[str, Any] = {}
        if fresh:
            out["emptied"] = self.clear_sandbox()
            # `clear_sandbox` asks whatever is running to stop, which is
            # right when a person presses Empty and wrong here: what
            # follows is this call's own doing.
            self.carry_on(SANDBOX)
            cap = self._max_states(self.where(SANDBOX))
            out["made_up"] = self.make_up_states(
                how_many=int(how_many) if how_many else max(12, int(cap * 0.9)),
                days=days, seed=seed)
        report = self.run_once(trigger="manual_debug", scope=SANDBOX)
        out["report"] = report
        return out

    def rehearse_append(self, first: int = 0, then: int = 40,
                        after: int = 12, days: int = 30,
                        seed: int = 0) -> Dict[str, Any]:
        """The whole appending mechanism, in two rounds, from empty.

        This is the one thing the sandbox exists to show, and showing it
        needs both halves:

        ROUND ONE puts in MORE states than the window holds, so that the
        cap is doing something rather than being a number in the settings.
        The grouping reads the newest `state_heartbeat_max_states` of them
        and NOTHING ELSE - the rest are before the beginning and are never
        read, not then and not later. That is the behaviour to look at
        hardest, because it is the one that loses information: if more
        than a window's worth arrives between two rounds, the oldest of
        them are never grouped at all. The window is a thousand states,
        which at the rate the review writes them is about ten days of
        catching up; a fortnight away is fine and a month away is not.

        ROUND TWO adds a few and runs again - and on a first grouping of
        any size it usually REBUILDS, which is worth seeing rather than
        arranging away. Round one leaves some states in no task at all,
        those are offered again every round, and a handful more arriving
        pushes the pile over the limit at which what fits nowhere counts
        as a task nobody has noticed. The rebuild is the right answer
        there, and the thing to look at is that it costs nothing in
        identity: the tasks keep their ids and only the summaries whose
        membership actually moved are written again.

        ROUND THREE adds a few more and shows the other path, which is
        the ordinary one: no signal fires, nothing is reclustered, each
        new state joins the task it is nearest in a few dot products,
        every summary is kept, and no model is called at all.
        """
        cap = self._max_states(self.where(SANDBOX))
        first = int(first) if first else int(cap * 1.2) + 1
        trace: List[Dict[str, Any]] = []

        cleared = self.clear_sandbox()
        # Emptying it was THIS rehearsal's own doing, so it is not an
        # interruption of the rehearsal. `clear_sandbox` asks whatever is
        # running to stop, which is right when a person presses the
        # button and wrong here: the flag would still be set when the
        # first round was skipped for any other reason, and the rehearsal
        # would report that the sandbox had been emptied underneath it.
        self.carry_on(SANDBOX)
        trace.append({"step": "emptied the sandbox", "detail": cleared})

        made = self.make_up_states(how_many=first, days=days, seed=seed)
        trace.append({
            "step": f"made up {made['written']} states over {days} days",
            "detail": {"subjects": made["subjects"],
                       "more_than_the_window_by": made["written"] - cap},
            "why": (f"More than the window holds ({cap}), so the cap is "
                    f"doing something rather than being a number in the "
                    f"settings.")})

        one = self.run_once(trigger="rehearsal", scope=SANDBOX)
        if one.get("skipped"):
            # Not "given up": nothing was interrupted, the round never
            # began. Said as what it is, because the two have different
            # answers - one is wait and try again, the other is that the
            # data went away.
            return {"cap": cap, "trace": trace, "skipped": one["skipped"],
                    "why": (f"The rehearsal did not start: "
                            f"{one['skipped']}. Nothing was changed.")}
        if not self._going(self.where(SANDBOX)):
            return {"cap": cap, "trace": trace, "given_up": True,
                    "why": self._given_up(self.where(SANDBOX))}
        window = one.get("window") or {}
        trace.append({
            "step": "round one: the first grouping",
            "detail": {
                "states_written": made["written"],
                "states_read": one.get("states_considered", 0),
                "never_read": window.get("before_the_beginning", 0),
                "rebuilt": (one.get("continuity") or {}).get("rebuilt"),
                "tasks": (one.get("continuity") or {}).get("kept_tasks", 0),
                "summaries_written": len(one.get("summaries_written") or []),
                "took_ms": one.get("duration_ms", 0)},
            "why": window.get("why", "")})

        more = self.make_up_states(how_many=int(then), days=1,
                                   seed=(seed or 0) + 1)
        trace.append({
            "step": f"then {more['written']} more states, written today",
            "detail": {"subjects": more["subjects"]}})

        two = self.run_once(trigger="rehearsal", scope=SANDBOX)
        if not self._going(self.where(SANDBOX)):
            return {"cap": cap, "trace": trace, "given_up": True,
                    "why": self._given_up(self.where(SANDBOX))}
        kept = two.get("continuity") or {}
        took = {task["id"]: task["kept"]["took"]
                for task in (two.get("layers") or []) if task["kept"]["took"]}
        trace.append({
            "step": "round two: what the new states did",
            "detail": {
                "arrivals": kept.get("arrivals", 0),
                "joined_a_task": kept.get("placed", 0),
                "matched_nothing": kept.get("loose", 0),
                "rebuilt": kept.get("rebuilt"),
                "why": kept.get("why", ""),
                "which_task_took_what": took,
                "summaries_written": len(two.get("summaries_written") or []),
                "summaries_kept": len(two.get("summaries_kept") or []),
                "took_ms": two.get("duration_ms", 0)},
            "why": ("Nothing was reclustered unless a signal fired. Every "
                    "signal is below, with how close it came.")})

        last = self.make_up_states(how_many=int(after), days=1,
                                   seed=(seed or 0) + 2)
        trace.append({
            "step": f"and {last['written']} more, to show the ordinary path",
            "detail": {"subjects": last["subjects"]}})

        three = self.run_once(trigger="rehearsal", scope=SANDBOX)
        third = three.get("continuity") or {}
        trace.append({
            "step": "round three: appending, with nothing rebuilt",
            "detail": {
                "arrivals": third.get("arrivals", 0),
                "joined_a_task": third.get("placed", 0),
                "matched_nothing": third.get("loose", 0),
                "rebuilt": third.get("rebuilt"),
                "why": third.get("why", ""),
                "pairs_scored": (three.get("similarity") or {}).get(
                    "pairs_scored", 0),
                "every_pair_scored": (three.get("similarity") or {}).get(
                    "scored_now"),
                "summaries_written": len(three.get("summaries_written") or []),
                "summaries_kept": len(three.get("summaries_kept") or []),
                "took_ms": three.get("duration_ms", 0)},
            "why": ("This is what an hour normally costs: a few dot "
                    "products against the task centres, and no tree. The "
                    "only model calls are for tasks that are new or whose "
                    "membership has moved enough to make what was written "
                    "about them wrong.")})

        started = {task["task_id"]: task["size"]
                   for task in (one.get("layers") or [])}
        ended = {task["task_id"]: task["size"]
                 for task in (three.get("layers") or [])}
        before, after_sizes = started, ended
        carried = sorted(set(before) & set(after_sizes))
        return {
            "cap": cap,
            "trace": trace,
            "identity": {
                "tasks_after_round_one": len(before),
                "tasks_at_the_end": len(after_sizes),
                "kept_their_id_throughout": len(carried),
                "grew": {task_id: [before[task_id], after_sizes[task_id]]
                         for task_id in carried
                         if after_sizes[task_id] != before[task_id]},
                "new": sorted(set(after_sizes) - set(before)),
                "gone": sorted(set(before) - set(after_sizes)),
            },
            "signals": third.get("signals", []),
            "round_one": one,
            "round_two": two,
            "round_three": three,
            "why": ("Round one reads the newest {cap} states and treats "
                    "everything older as seen. Round two usually rebuilds, "
                    "because a first grouping leaves states in no task and "
                    "those pile up - and it keeps every id while doing it. "
                    "Round three is the ordinary hour: appending only, no "
                    "tree, no model call.").format(cap=cap),
        }

    def sandbox_status(self) -> Dict[str, Any]:
        """Where the sandbox is and what is in it, without running anything."""
        where = self.where(SANDBOX)
        from .tasks import Kept as _Kept

        kept = _Kept(self.db, SANDBOX)
        return {
            "where": where.to_dict(),
            "states": len(where.library.records(STATES)),
            "tasks_written": len(where.library.records(STATE_TASKS)),
            "tasks_kept": len(kept.live()),
            "runs": len(kept.runs(limit=20)),
            "last_report": self._sandbox_report or {},
        }

    #: Everything the sandbox writes into the database, all of it scoped.
    #:
    #: Listed in one place because emptying the sandbox has to name every
    #: one of them: the loose pile was added after this was first written
    #: and was not cleared, so an emptied sandbox kept remembering states
    #: that no longer existed.
    SANDBOX_TABLES = ("state_tasks", "state_task_runs", "state_task_loose",
                      "state_projects")

    def clear_sandbox(self) -> Dict[str, Any]:
        """Empty the sandbox, and say what was in it.

        Two kinds of thing, which are easy to confuse and worth counting
        apart. FILES are what a person can open: the made-up states, and
        the write-up of each task found from them. ROWS are the
        bookkeeping behind them - which tasks there are, what each one's
        centre is, what every round did, and which states fitted nowhere.
        They do not correspond one to one: a task is one row and one
        write-up over many state files, and a task found but not yet
        summarised is a row with no file at all.
        """
        where = self.where(SANDBOX)
        # ASKED TO STOP FIRST, and only then deleted. A round that is
        # halfway through reading a thousand states will otherwise finish
        # against files that are being removed underneath it, and write
        # tasks about states that no longer exist.
        self.abandon(SANDBOX)
        files: Dict[str, int] = {}
        for folder in (STATES, STATE_TASKS):
            gone = 0
            for path in list(where.library.files(folder)):
                try:
                    Path(path).unlink()
                    gone += 1
                except OSError:      # pragma: no cover - a sandbox file
                    log.debug("could not remove %s", path, exc_info=True)
            files[folder] = gone
        rows: Dict[str, int] = {}
        if self.db is not None:
            for table in self.SANDBOX_TABLES:
                try:
                    cursor = self.db.execute(
                        f"DELETE FROM {table} WHERE scope = ?", (SANDBOX,))
                    rows[table] = int(getattr(cursor, "rowcount", 0) or 0)
                except Exception:    # pragma: no cover
                    log.debug("could not clear %s", table, exc_info=True)
                    rows[table] = 0
        self._sandbox_report = {}
        self._inside = {}
        self._marks.pop(SANDBOX, None)
        self._fresh_ids.pop(SANDBOX, None)
        # The flag is NOT cleared here. Clearing it at the end of this was
        # the first attempt and it undid the whole thing: emptying nine
        # hundred files takes six seconds, which is longer than the gap
        # between a round's checks, so the round looked, saw nothing set,
        # and carried on for another twenty-six seconds. It is cleared
        # when the next round starts instead - that round is not the one
        # being given up on.
        return {
            "where": where.to_dict(),
            "files_removed": sum(files.values()),
            "rows_removed": sum(rows.values()),
            "files": {"made-up states": files.get(STATES, 0),
                      "task write-ups": files.get(STATE_TASKS, 0)},
            "rows": {"tasks": rows.get("state_tasks", 0),
                     "rounds": rows.get("state_task_runs", 0),
                     "states that fitted nowhere":
                         rows.get("state_task_loose", 0)},
            "why": ("Files are what you can open - the states and the "
                    "write-up of each task. Rows are the bookkeeping: which "
                    "tasks there are, what each round did, and which states "
                    "fitted nowhere. They do not match one to one."),
        }

    # -- the two places a round can run ------------------------------------
    def where(self, scope: str = LIVE) -> _Where:
        """The real library and store, or the sandbox's.

        Built on demand: the sandbox's folders are not made until somebody
        asks for a sandbox round, so an installation that never opens the
        debugger never grows a directory it did not ask for.
        """
        if str(scope) != SANDBOX:
            return _Where(LIVE, self.states, self.library, LIVE)
        held = getattr(self, "_sandbox", None)
        if held is None:
            from ...data_manage.library import DataLibrary

            root = self._sandbox_root()
            library = DataLibrary(root)
            library.ensure()
            held = _Where(SANDBOX, _SandboxStates(library), library, SANDBOX)
            self._sandbox = held
        return held


    def _sandbox_root(self) -> Path:
        configured = str(self.config.get("state_sandbox_root", "") or "")
        if configured:
            return Path(configured)
        root = str(self.config.get("agent_root", "") or "")
        return Path(root) / "internal" / "state_sandbox" if root else Path(
            "state_sandbox")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # STARTING UP IS A FRESH CHANCE AT WHAT IS STILL BLANK.
        #
        # "This round has tried everything it can" is true of the round
        # it was written in, and it is what stops the same three answers
        # being asked for again a minute later. It should not outlive the
        # process: somebody who closes the application and opens it again
        # is asking for a look at where things stand, and the model that
        # gave nothing an hour ago may answer now. So the note is dropped
        # here, and anything still without words gets one more go.
        #
        # Once per start, not once per minute - the round still gets a
        # single attempt at each task, and then it is exhausted again.
        try:
            if self._how_much_is_blank():
                self.config.set("state_round_exhausted", False, internal=True)
                # AND FORGET THE BLANKS WERE ASKED - only the blanks.
                #
                # Clearing only the "nothing left to try" note let the
                # round resume and skip every blank, because each was
                # still on the list of what it had asked: picked up after
                # a restart, zero calls, eight still blank.
                #
                # Clearing the WHOLE list was the next mistake. A task
                # with a name is not skipped by its name alone - one that
                # has grown is described again - so forgetting that it had
                # been asked sent named work to be renamed on every
                # restart: the messaging task, named and in plain view, was
                # one of the four calls. Only what is still blank is
                # forgotten; what has words keeps them.
                blank = self._still_missing(everything=True)
                still = set()
                for one in blank:
                    still.add(one)                    # a task's own id
                    if "/" in one:
                        still.add(one.split("/", 1)[1])  # a piece's id
                kept = [one for one in self._tried_this_round()
                        if one not in still]
                self.config.set("state_round_tried", json.dumps(kept),
                                internal=True)
                self._forget_recent_answers()
                log.info("starting up with %d task(s) or piece(s) still "
                         "blank, so this round has another go at them",
                         self._how_much_is_blank())
        except Exception:        # pragma: no cover - a note, not the work
            log.debug("could not look for blanks at start-up", exc_info=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="state-heartbeat",
                                        daemon=True)
        self._thread.start()
        log.info("state heartbeat started")

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
        log.info("state heartbeat stopped")

    #: How deep the search for something worth showing goes.
    DEEPEST_SUMMARY = DEEPEST_SUMMARY

    #: The settings this loop's timing depends on.
    WAKES_ON = ("state_heartbeat_every_hours", "state_heartbeat_enabled")

    # -- when a round is due, and what a half-finished one leaves behind --
    #
    # A round is not a tick. It summarises every task it finds, one model
    # call at a time, so on a busy folder it is minutes of work - and the
    # tasks it has not reached yet say so rather than showing last
    # round's words as if they were this round's. That makes a round a
    # thing with a beginning, a middle and an end, and all three have to
    # survive the application being closed in the middle.
    #
    #   started_at    when this round began asking
    #   reads_until   the moment it draws its line at. States that arrive
    #                 WHILE it runs are not in it: a round reading a
    #                 moving window would summarise a task, have three
    #                 more states land in it, and finish holding a
    #                 summary that was already out of date - with nothing
    #                 to tell that apart from one that was never written.
    #   done          whether it reached the end
    #
    # Kept in the settings, which is where this project already keeps the
    # facts a restart has to remember, beside the backfill marks.

    def _their_clock(self, moment: str) -> str:
        """A stored moment, said where the user is standing.

        Everything is stored in UTC, which is right - one clock and
        arithmetic that survives a move - and "2026-10-01T15:23:16+00:00"
        is not a time anybody recognises as their own afternoon. The zone
        is named in the result because a bare local time is worse than a
        UTC one: it looks familiar and cannot be checked.
        """
        if not moment:
            return "an unrecorded time"
        try:
            when = datetime.fromisoformat(str(moment))
        except ValueError:
            return str(moment)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        from ...agent.states.store import zone_named

        zone = zone_named(str(self.config.get("states_zone_before", "")) or "")
        here = when.astimezone(zone) if zone is not None else when.astimezone()
        name = here.tzname() or ""
        # THE YEAR TOO. A summary is read months after it was written as
        # often as minutes after, and "Thu 01 Oct 15:15" is the same
        # string every year - which is exactly the case where knowing how
        # old something is matters most.
        return (here.strftime("%a %d %b %Y %H:%M")
                + (f" {name}" if name else ""))

    def what_it_is_doing(self) -> Dict[str, Any]:
        """Whether a round is in flight, and what it has reached.

        ONE SHAPE FOR ALL THREE HEARTBEATS, because the thing reading it -
        a status line with room for a few words - cannot ask three
        different questions and does not care which heartbeat answered.
        The stage comes from the round's own report, so it names the step
        rather than saying "busy": somebody watching a quit that will not
        finish, or a round that is taking minutes, needs to know WHICH
        part is taking the time.
        """
        held = self.progress() or {}
        busy = bool(held.get("running"))
        return {
            "busy": busy,
            "doing": "grouping states" if busy else "",
            "stage": str(held.get("stage") or "") if busy else "",
            "detail": str(held.get("stage_detail") or "") if busy else "",
            "since": held.get("started_at", "") if busy else "",
        }

    def _let_go_of_waiting(self) -> None:
        """Nothing is owed a description once the round has ended.

        The note says a round means to describe this task. When the round
        is over that is no longer true of anything, whether it got there
        or not - the next round will decide again. The words themselves
        are not touched here, because they were never taken away.
        """
        try:
            kept = Kept(self.db, LIVE)
            live = list(kept.live())
        except Exception:        # pragma: no cover - a read, not a step
            return
        put_back = []
        for task in live:
            held = dict(task.described or {})
            if not held.get("waiting") and not held.get("was"):
                continue
            held.pop("waiting", None)
            held.pop("waiting_size", None)
            # And anything an older build moved aside comes home.
            was = dict(held.pop("was", None) or {})
            for name in ("title", "summary", "status"):
                if held.get(name) in (WAITING, *WAITING_BEFORE):
                    held.pop(name, None)
                if not held.get(name) and was.get(name) not in (
                        None, WAITING, *WAITING_BEFORE):
                    held[name] = was[name]
            task.described = held
            put_back.append(task)
        if not put_back:
            return
        try:
            kept.save(put_back)
            log.info("cleared the note on %d task(s) the round did not reach",
                     len(put_back))
        except Exception:        # pragma: no cover - a save, not a step
            log.debug("could not clear the notes", exc_info=True)

    def _named_this_round(self) -> List[str]:
        """The tasks this round has already written up.

        WITHOUT THIS A RESUMED ROUND STARTS AGAIN FROM THE TOP. The marking
        step tells every task it is going to describe to say "loading", and
        the loop asks about each in turn - so a round interrupted after
        naming nine of eleven came back, told all eleven to say loading
        again, and paid for the nine a second time. The names were on the
        tasks the whole time; nothing knew they belonged to the round still
        in progress.

        Kept beside the round's own line and cleared when a new line is
        drawn, because that is exactly the span it describes.
        """
        try:
            held = json.loads(
                str(self.config.get("state_round_named", "[]")) or "[]")
            return [str(one) for one in held] if isinstance(held, list) else []
        except (TypeError, ValueError):
            return []

    def _tried_this_round(self) -> List[str]:
        """The tasks this round has ASKED about, named or not.

        Not the same list as what it named, and the difference is what
        stops a rule from becoming a deadlock. The pieces of a task wait
        until every task at the top has a name - but a task can be asked
        about and come back with nothing usable, and then it never has
        one. Waiting on that forever means no round ever finishes and
        nothing below the top layer is ever written again.

        So the pieces wait for every top-layer task to have been ASKED
        about, which a round can always finish, rather than to have
        succeeded, which it cannot promise.
        """
        try:
            held = json.loads(
                str(self.config.get("state_round_tried", "[]")) or "[]")
            return [str(one) for one in held] if isinstance(held, list) else []
        except (TypeError, ValueError):
            return []

    def _also_tried(self, which: str) -> None:
        """Note that this round asked about one, whatever came back."""
        if not which:
            return
        held = self._tried_this_round()
        if which in held:
            return
        held.append(which)
        try:
            self.config.set("state_round_tried", json.dumps(held),
                            internal=True)
        except Exception:        # pragma: no cover - a note, not the work
            log.debug("could not record what this round asked about",
                      exc_info=True)

    def _also_named(self, which: str) -> None:
        """Add one to that list, as soon as it is written."""
        if not which:
            return
        held = self._named_this_round()
        if which in held:
            return
        held.append(which)
        try:
            self.config.set("state_round_named", json.dumps(held),
                            internal=True)
        except Exception:        # pragma: no cover - a note, not the work
            log.debug("could not record what this round named", exc_info=True)

    def _grouping_file(self) -> Path:
        """Where this round's grouping is kept."""
        return Path(self.db.path).parent / "state_round_grouping.json"

    def _bring_back_the_pieces(self, report: Dict[str, Any]) -> None:
        """Give each layer the pieces its stored task holds.

        Only where the layer has none of its own: a layer that does was
        built this visit and is the fresher of the two.
        """
        try:
            stored = {task.id: task for task in Kept(self.db, LIVE).live()}
        except Exception:        # pragma: no cover - a read, not a step
            return
        brought = 0
        for layer in report.get("layers") or []:
            if layer.get("children"):
                continue
            task = stored.get(str(layer.get("task_id") or ""))
            pieces = list(((task.described if task else None) or {})
                          .get("pieces") or [])
            if not pieces:
                continue
            layer["children"] = [_a_piece_as_drawn(one) for one in pieces]
            brought += 1
        if brought:
            report["pieces_read_back"] = brought
            log.info("read the pieces of %d task(s) back from where they "
                     "were kept", brought)

    def _mark_the_round_decided(self, nodes) -> None:
        """Say that this round's shape is settled, if nothing has yet.

        The scoring writes a fuller note - the merges, which are the
        costly part - and this only fills in for the rounds that did not
        score anything. What both mean is the same: the grouping, the
        task set and the samples belong to this round now, and a later
        visit has only to finish the naming.
        """
        path = self._grouping_file()
        which = str(self.config.get("state_round_started_at", ""))
        try:
            held = json.loads(path.read_text(encoding="utf-8"))
            if str(held.get("round") or "") == which:
                return              # the scoring already wrote one
        except (OSError, ValueError):
            pass
        try:
            path.write_text(json.dumps({
                "round": which,
                "states": [node.id for node in nodes],
                "merges": [],
                "samples": {},
                "why": "this round did not rebuild, so there are no merges "
                       "to keep - only the fact that it has decided",
            }), encoding="utf-8")
        except Exception:        # pragma: no cover - a note, not the work
            log.debug("could not mark the round as decided", exc_info=True)

    def _keep_grouping(self, nodes, merges, shown, counted, anchored) -> None:
        """Keep what the scoring found, for the rest of this round.

        Written beside the database rather than into a setting: it is a
        few hundred kilobytes of working-out, which is not a setting, and
        it is worthless the moment the round ends.
        """
        try:
            self._grouping_file().write_text(json.dumps({
                "round": str(self.config.get("state_round_started_at", "")),
                "states": [node.id for node in nodes],
                # A Merge is an object, and this file is read back by a
                # later visit in another process, so it goes out as the
                # four numbers it is made of.
                "merges": [one.to_dict() for one in merges],
                "pairs": shown,
                "scored": counted,
                "anchored": anchored,
            }), encoding="utf-8")
        except Exception:            # pragma: no cover - a note, not the work
            log.warning("could not keep this round's grouping", exc_info=True)

    def _grouping_kept(self, nodes):
        """The grouping this round already made, if it still fits.

        It must be THIS round's and cover EXACTLY these states in this
        order - the merges are positions into that list, so a grouping
        read against a different list would quietly join the wrong
        states together. Anything that does not match is ignored, and
        the scoring runs again.
        """
        record = self._round_record()
        if record["done"] or not record["started_at"]:
            return None
        try:
            held = json.loads(self._grouping_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if str(held.get("round") or "") != record["started_at"]:
            return None
        if list(held.get("states") or []) != [node.id for node in nodes]:
            return None
        merges = held.get("merges")
        if not isinstance(merges, list) or not merges:
            # A round that did not rebuild leaves the note with no merges
            # in it. That is enough to say the deciding is done, and
            # nothing for the scoring to pick up.
            return None
        try:
            held["merges"] = [Merge(int(one["left"]), int(one["right"]),
                                    float(one["height"]), int(one["size"]))
                              for one in merges]
        except (KeyError, TypeError, ValueError):
            return None
        return held

    def _spent_on(self, key: str) -> Dict[str, Any]:
        try:
            held = json.loads(str(self.config.get(key, "") or "") or "{}")
        except ValueError:
            return {}
        return held if isinstance(held, dict) else {}

    def _add_what_this_visit_spent(self, report: Dict[str, Any]) -> None:
        """Add one visit's calls, tokens and time to its round's total.

        A ROUND IS ITS VISITS. One stopped halfway by a quit and finished
        on the next start is one round, and what it cost is both halves -
        so each visit adds to the running total rather than replacing it,
        and the total is kept as the last complete round's once the work
        is all there.

        The visit's own part is the tasks and projects; the stages it
        writes at its end are a part of their own, counted as they finish,
        and so are taken out of its time here.
        """
        if (report.get("where") or {}).get("scope") == SANDBOX:
            return
        if report.get("skipped"):
            return
        stages = report.get("stages") or []
        used = {"calls": sum(int(one.get("calls") or 0) for one in stages),
                "tokens_in": sum(int(one.get("tokens_in") or 0)
                                 for one in stages),
                "tokens_out": sum(int(one.get("tokens_out") or 0)
                                  for one in stages)}
        took = int(report.get("duration_ms") or 0) / 1000.0
        ended = time.time()
        writing = float((report.get("experience") or {}).get("seconds") or 0)
        with self._spend_lock:
            held = self._round_account()
            _add_part(held, "tasks", ended - took, ended, used,
                      seconds=max(0.0, took - writing))
            held["visits"] = int(held.get("visits") or 0) + 1
            self.config.set("state_round_spent", json.dumps(held),
                            internal=True)
        try:
            record = self._round_record()
        except Exception:        # pragma: no cover - a read, not a step
            return
        if record.get("done"):
            with self._spend_lock:
                held = self._round_account()
                finished = dict(held, completed_at=record.get("completed_at")
                                or datetime.now(timezone.utc).isoformat())
                self.config.set("state_last_round_spent", json.dumps(finished),
                                internal=True)

    def _round_account(self) -> Dict[str, Any]:
        return self._spent_on("state_round_spent") or {
            "calls": 0, "tokens_in": 0, "tokens_out": 0, "seconds": 0.0,
            "visits": 0,
            "started_at": str(self.config.get("state_round_started_at", ""))}

    def _spend(self, part: str, began: float, ended: float,
               used: Dict[str, int]) -> None:
        """One part of the round's cost, into its account.

        A ROUND IS SEVERAL PARTS AT ONCE - its tasks and projects, the
        stages of the work, and the work and personal patterns built beside
        it - and what it took is the time while ANY of them was running,
        not their sum. Each part keeps its own time and tokens; the round's
        time is the union of their windows, its tokens their sum. A part
        that finishes after the round's tasks did is added to the finished
        round's account as well.
        """
        with self._spend_lock:
            held = self._round_account()
            _add_part(held, part, began, ended, used)
            self.config.set("state_round_spent", json.dumps(held),
                            internal=True)
            last = self._spent_on("state_last_round_spent")
            if last and last.get("started_at") == held.get("started_at"):
                _add_part(last, part, began, ended, used)
                self.config.set("state_last_round_spent", json.dumps(last),
                                internal=True)

    def _round_record(self):
        """What the last round said about itself."""
        return {
            "started_at": str(self.config.get("state_round_started_at", "")),
            "reads_until": str(self.config.get("state_round_reads_until", "")),
            # FINISHED MEANS THE WORK IS THERE.
            #
            # The stored flag says a round reached its end, and that was
            # read as "finished" everywhere - including on the panel,
            # which showed "round finished" above "1 of 4 tasks named".
            # Both came from this call. A round is finished when what it
            # set out to write exists, so the rows have the last word and
            # the flag is one of the two things that has to be true.
            "done": (bool(self.config.get("state_round_done", True))
                     and not self._how_much_is_blank()),
            # What the round itself recorded, for anything that needs to
            # know whether it got to the end of its own loop.
            "reached_the_end": bool(
                self.config.get("state_round_done", True)),
            # Had its go at everything it could, which is not the same as
            # having got an answer for all of it.
            "exhausted": bool(self.config.get("state_round_exhausted", False)),
            # WHICH KIND OF ROUND THIS IS. A visit that opens one reads
            # every state, scores every pair and decides what the tasks
            # are; a visit finishing one has all of that already and does
            # nothing but write the names that are missing. They cost
            # quite different amounts and look identical from outside.
            "carrying_on": self._resuming_a_round(),
            # The model could not be reached, and why - so the page says
            # that, rather than that the round has tried everything.
            "blocked": str(self.config.get("state_round_blocked_why", "")
                           or "") if self.config.get(
                               "state_round_blocked_at") else "",
            # AND WHAT THE WORK ITSELF SAYS, asked fresh rather than
            # remembered. The flag is written when a round ends and
            # describes that moment; the rows go on changing, and a round
            # marked finished with three tasks still blank is a claim the
            # rows do not support. Whoever reads this gets the answer the
            # panel is showing.
            "blank": self._how_much_is_blank(),
            "completed_at": str(
                self.config.get("state_round_completed_at", "")),
            # What it has cost so far, every visit together; and what the
            # last round to finish cost in all.
            "spent": self._spent_on("state_round_spent"),
            "last_spent": self._spent_on("state_last_round_spent"),
        }

    def _every_hours(self) -> float:
        # A MINUTE IS THE FLOOR, which is what the interval in seconds
        # allowed before this replaced it. Nobody runs it that fast for
        # real - a round is minutes of work - but somebody watching one
        # happen should not have to wait six hours to see the next, and
        # taking that away while renaming the setting would be removing
        # a capability under cover of a tidy-up.
        return max(1.0 / 60.0,
                   float(self.config.get("state_heartbeat_every_hours", 12.0)))

    def _nothing_to_show(self) -> bool:
        """Whether the panel has a picture to draw at all.

        A picture needs two tasks - a dependency drawing of one task is a
        circle with nothing to depend on - so one task counts as nothing
        to show here, which is the same thing the drawing itself decides.
        """
        held = self.last_report or {}
        if len((held.get("plan") or {}).get("nodes") or []) >= 2:
            return False
        try:
            return len(Kept(self.db, LIVE).live()) < 2
        except Exception:        # pragma: no cover - a read, not a step
            return False

    def how_many_wait(self) -> Dict[str, Any]:
        """How many states exist, and how many are in no task yet.

        COUNTED IN SQL, not by reading them. The first version of this
        loaded every state through the same path a round uses, and it is
        asked on every status poll - several times a minute, from a panel
        that is only trying to print a number. Two sums and a count
        answer it.

        "Not yet in a task" rather than "unread": they are the same on a
        fresh install and they come apart afterwards, and the one a reader
        means by "not summarised" is this one - a state the grouping has
        seen and left in no task is not summarised either, and it is
        exactly what a reader is looking for when the picture seems thin.

        Only the states a round would ever read are counted. The calendar
        also holds what the agent wrote about its own scheduled work, and
        including those would report a backlog that no round will ever
        touch.
        """
        out = {"states": 0, "in_tasks": 0, "waiting": 0, "tasks": 0}
        try:
            counts = self.states.counts(OBSERVED)
            out["states"] = int(counts.get("active", 0)) + int(
                counts.get("done", 0))
        except Exception:        # pragma: no cover - a read, not a step
            log.debug("could not count the states", exc_info=True)
        try:
            # The membership is a JSON list, and `total` is NOT a count -
            # it holds the task's vector, so summing it returned a number
            # made of bytes. Counted from the lists, which is a handful of
            # rows: the tasks are tens, not thousands.
            held = set()
            tasks = 0
            for row in self.db.query(
                    "SELECT members FROM state_tasks "
                    "WHERE scope = ? AND retired_at IS NULL", (LIVE,)):
                tasks += 1
                try:
                    held.update(json.loads(row["members"] or "[]"))
                except (TypeError, ValueError):
                    continue
            out["tasks"] = tasks
            # A SET, so a state counted twice is counted once: if two
            # tasks ever claimed the same state, adding the lengths would
            # report more states held than exist and a negative backlog.
            out["in_tasks"] = len(held)
        except Exception:        # pragma: no cover - a read, not a step
            log.debug("could not count what the tasks hold", exc_info=True)
        out["waiting"] = max(0, out["states"] - out["in_tasks"])
        return out

    def _never_read(self) -> int:
        """How many states are in no task yet."""
        return int(self.how_many_wait()["waiting"])

    def due_now(self) -> Dict[str, Any]:
        """Whether a round is due, and the reason either way.

        Asked when an agent heartbeat finishes. The reason comes back
        rather than only going to the log, because "it did not run" and
        "it ran and found nothing" look identical from outside and the
        panel has to be able to say which.
        """
        held = self._round_record()
        # ON EVERY ANSWER, because "nothing to draw" and "nothing to draw
        # AND four hundred states sitting in no task" are different things
        # to be told, and the second is the one that explains the first.
        counted = self.how_many_wait()
        if not bool(self.config.get("state_heartbeat_enabled", True)):
            return {"run": False, "resuming": False, "counted": counted,
                    "why": "The state heartbeat is turned off."}
        from ...common.modules import enabled as module_on
        if not module_on(self.config, "tasks"):
            return {"run": False, "resuming": False, "counted": counted,
                    "why": "Task organisation is turned off in Settings > Modules."}
        # AN UNFINISHED ROUND IS CARRIED ON ONLY IF IT HAS SOMETHING LEFT
        # TO TRY. It stays unfinished either way - three tasks with no
        # words on them is not a finished round, and saying so is the
        # point - but picking it up every minute to look at the same
        # three answers that did not work is not carrying it on.
        # THE ROWS DECIDE WHETHER THERE IS WORK, NOT THE FLAG. A round
        # that ended leaving tasks blank is unfinished whatever it wrote
        # about itself at the time - the panel can see those blanks and
        # so can this. What the flag is still for is stopping a round
        # that has just tried them from trying again a minute later.
        # TASKS AND PROJECTS (section 34.5): the first run takes over at
        # once and reads the last week; until something has been written,
        # any state is enough to begin. The scheme before it - rounds of
        # visits carried on, twenty states first - does not apply.
        scheme = self._projects_scheme()
        if scheme:
            from .project_round import SWITCHED_AT
            from .task_projects import RunTasks
            if not self.config.get(SWITCHED_AT, ""):
                return {"run": True, "resuming": False, "counted": counted,
                        "why": ("Tasks and projects take over: the first run "
                                "reads the last week's states.")}
            if RunTasks(self.db, LIVE).count() == 0:
                waiting = self._never_read()
                return {"run": waiting > 0, "resuming": False, "unread": waiting,
                        "counted": counted,
                        "why": (f"Nothing written yet; {waiting} state(s) to read."
                                if waiting else "Nothing written yet, and no state "
                                                "to read.")}
        unfinished = (not held["done"] or bool(held.get("blank"))) and not scheme
        waiting = self._still_blocked() if unfinished else None
        if waiting:
            return {"run": False, "resuming": True, "counted": counted,
                    "blocked": waiting["why"],
                    "why": ("The model could not be reached - "
                            + waiting["why"] + ". The round is kept open and "
                            "tried again at " + waiting["again_said"]
                            + ", or at once when the sign-in comes back.")}
        if unfinished and not bool(
                self.config.get("state_round_exhausted", False)):
            return {"run": True, "resuming": True,
                    "why": ("A round that began "
                            + self._their_clock(held["started_at"])
                            + " did not finish, so it is being resumed "
                            "rather than waiting its hours - the tasks it "
                            "never reached have no name yet.")}
        # WHILE THERE IS NOTHING TO SHOW, THE COUNT IS THE WHOLE RULE and
        # the hours do not apply. Two reasons, and they point the same way:
        # a dozen states grouped into a dozen tasks of one is a drawing of
        # a list, which is what grouping exists to replace; and six hours
        # of an empty panel reads as a broken panel, because a reader has
        # no way to tell waiting from failing. So below the threshold
        # nothing runs and the panel SAYS it is waiting and for what;
        # above it, the first round goes at once. Once a picture exists
        # this stops applying and the hours take over.
        # Twenty: a hundred kept a new installation's Overview empty for a
        # day or two of use, and twenty is enough for a first few tasks of
        # five (the user asked for it).
        enough = max(1, int(self.config.get("state_first_round_states", 20)))
        if self._nothing_to_show() and not scheme:
            waiting = self._never_read()
            if waiting < enough:
                return {"run": False, "resuming": False, "unread": waiting,
                        "waiting_for": enough, "counted": counted,
                        "why": (f"Waiting for the first {enough} states "
                                f"before anything is grouped - {waiting} so "
                                f"far. Fewer than that is a list, and a list "
                                f"is what grouping replaces.")}
            return {"run": True, "resuming": False, "unread": waiting,
                    "waiting_for": enough, "counted": counted,
                    "why": (f"Nothing has been grouped yet and there are "
                            f"{waiting} states to read, past the {enough} "
                            f"the first round waits for.")}
        every = self._every_hours()
        if not held["completed_at"]:
            return {"run": True, "resuming": False,
                    "why": "No round has ever finished."}
        try:
            was = datetime.fromisoformat(held["completed_at"])
        except ValueError:
            return {"run": True, "resuming": False,
                    "why": ("The last completion time cannot be read, so it "
                            "is treated as never.")}
        if was.tzinfo is None:
            was = was.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - was).total_seconds() / 3600.0
        waiting = int(counted["waiting"])
        tail = (f" {waiting} state(s) are in no task yet, of "
                f"{counted['states']} the grouping reads."
                if waiting else
                f" Every one of the {counted['states']} states it reads is "
                f"in a task.")
        if hours >= every:
            return {"run": True, "resuming": False, "hours": round(hours, 2),
                    "counted": counted,
                    "why": (f"{hours:.1f} hours since the last round "
                            f"finished, past the {every:g} it waits." + tail)}
        # TWO QUESTIONS, AND NO THIRD ONE.
        #
        # Every minute: is there a round that did not finish - carry it
        # on; otherwise, has it been long enough since the last one
        # finished. That is the whole rule.
        #
        # A third was added here, that a task with no name should not
        # have to wait six hours for one, and it ran the agent in a
        # circle: beginning a round clears the record of what the last
        # one asked about, which was the very thing stopping it from
        # asking again - so a task the model could not name made a round
        # due, every minute, for ever. The hours are what stops that, and
        # a hole in the picture is not worth paying for a round a minute
        # to keep looking at.
        return {"run": False, "resuming": False, "hours": round(hours, 2),
                "counted": counted,
                "why": (f"{hours:.1f} hours since the last round finished, "
                        f"and it waits {every:g}." + tail)}

    def _undo_the_places_regroup(self, kept: "Kept") -> None:
        """THE REGROUP A NEW READING OF PLACES FORCED, undone - once.

        For a few hours one build recorded how places are read among the
        settings of the tree, so starting it rebuilt every task: twelve
        pieces of work merged into one, most states in none. Its rebuild is
        the only one whose record carries `places_read_as`. Undone: the
        tasks it retired are live again, with their names, summaries and
        projects; the tasks it made are retired; and its record no longer
        counts as the last rebuild. The states since are placed again by
        the next rounds, as new states are."""
        if self.config.get("state_places_regroup_undone"):
            return
        try:
            rows = self.db.query(
                "SELECT id, ran_at, lineage, shape FROM state_task_runs "
                "WHERE scope = ? AND rebuilt = 1 ORDER BY ran_at DESC LIMIT 1", (LIVE,))
        except Exception:        # pragma: no cover - no such table yet
            return
        if not rows:
            self.config.set("state_places_regroup_undone", True, internal=True)
            return
        run = dict(rows[0])

        def loaded(value: Any, empty: Any) -> Any:
            try:
                said = json.loads(value) if isinstance(value, str) else value
            except ValueError:
                return empty
            return said if isinstance(said, type(empty)) else empty
        shape = loaded(run.get("shape"), {})
        if "places_read_as" not in shape:
            self.config.set("state_places_regroup_undone", True, internal=True)
            return
        lineage = [one for one in loaded(run.get("lineage"), []) if isinstance(one, dict)]
        made = [str(one.get("task")) for one in lineage
                if one.get("task") and one.get("kind") in ("merged from", "split from", "new")]
        retired_from = {str(old) for one in lineage for old in (one.get("from") or [])}
        # And what it retired without making anything of it: retired within
        # a minute of the run, by the run.
        at = str(run.get("ran_at") or "")
        try:
            moment = datetime.fromisoformat(at)
            lo = (moment - timedelta(minutes=2)).isoformat()
            hi = (moment + timedelta(minutes=2)).isoformat()
            for row in self.db.query(
                    "SELECT id FROM state_tasks WHERE scope = ? AND retired_at >= ? "
                    "AND retired_at <= ?", (LIVE, lo, hi)):
                retired_from.add(str(dict(row)["id"]))
        except ValueError:
            pass
        retired_from -= set(made)
        for task_id in sorted(retired_from):
            self.db.execute("UPDATE state_tasks SET retired_at = NULL, updated_at = ? "
                            "WHERE id = ? AND scope = ?",
                            (datetime.now(timezone.utc).isoformat(), task_id, LIVE))
        if made:
            kept.retire(made)
        self.db.execute("UPDATE state_task_runs SET rebuilt = 0 WHERE id = ?", (run["id"],))
        self.config.set("state_places_regroup_undone", True, internal=True)
        log.warning("undid the regroup of %s: %d tasks live again, %d retired",
                    at, len(retired_from), len(made))

    def _begin_round(self, where: _Where) -> str:
        """Draw the line this round reads up to, and say it has begun."""
        until = datetime.now(timezone.utc).isoformat()
        if where.scope == SANDBOX:
            return until
        held = self._round_record()
        # A RESUMED ROUND KEEPS THE OLD LINE. Moving it would pull in
        # states that arrived during the part which already ran, and the
        # tasks summarised before the interruption would be the only ones
        # that had not seen them.
        # A ROUND THAT STILL HAS SOMETHING TO TRY KEEPS ITS LINE. One
        # that has tried everything it can does not: it stays marked
        # unfinished, because the work is not there, but the next round
        # reads up to NOW and may get a better answer from a model that
        # is no longer having a bad day. Keeping the old line for ever
        # would mean never reading another state.
        if (not held["done"] and held["reads_until"]
                and not bool(self.config.get("state_round_exhausted", False))):
            return held["reads_until"]
        # A NEW ROUND, so nothing has been named in it yet. Cleared here
        # rather than at the end, because the end is the one thing a round
        # cannot be relied upon to reach.
        self.config.set("state_round_named", "[]", internal=True)
        self.config.set("state_round_tried", "[]", internal=True)
        self.config.set("state_round_exhausted", False, internal=True)
        self.config.set("state_round_status", "[]", internal=True)
        # And a new round groups for itself.
        try:
            self._grouping_file().unlink(missing_ok=True)
        except OSError:              # pragma: no cover - a file, not the work
            pass
        self.config.set("state_round_started_at", until, internal=True)
        # WHAT THE ROUND COSTS, from nothing - and added to by every visit
        # it takes, an interrupted one included.
        self.config.set("state_round_spent", json.dumps(
            {"calls": 0, "tokens_in": 0, "tokens_out": 0, "seconds": 0.0,
             "visits": 0, "started_at": until}), internal=True)
        self._forget_recent_answers()
        self.config.set("state_round_reads_until", until, internal=True)
        self.config.set("state_round_done", False, internal=True)
        return until

    def _answered_recently(self, key: str, seconds: float, make):
        """An answer kept for a moment, because the page asks constantly.

        THESE ARE STATUS QUESTIONS AND THEY WERE COSTING A SECOND EACH.
        How many tasks are blank, which kind of round is running, what
        work is ahead - each reads the whole task table or parses the
        round's grouping file, and the panel asks for a drawing roughly
        once a second, which asks all of them several times over. The
        application stopped feeling responsive and the cause was the
        status, not the work.

        A second of staleness is nothing here: these answers change when
        a round writes something, which is minutes apart, and the next
        poll picks it up.
        """
        now = time.time()
        held = getattr(self, "_recent_answers", None)
        if held is None:
            held = {}
            self._recent_answers = held
        when, what = held.get(key, (0.0, None))
        if now - when < seconds:
            return what
        what = make()
        held[key] = (now, what)
        return what

    def _forget_recent_answers(self) -> None:
        """Drop them, for when something has just changed them."""
        self._recent_answers = {}

    def _not_worth_drawing(self) -> set:
        """The stored tasks a summary judged not worth showing."""
        def look():
            try:
                return {task.id for task in Kept(self.db, LIVE).live()
                        if (task.described or {}).get("concrete") is False}
            except Exception:    # pragma: no cover - a read, not a step
                return set()
        return set(self._answered_recently("hidden", 2.0, look) or set())

    def _leisure_tasks(self) -> set:
        """The stored tasks of LEISURE: most of their states whose kind was
        said, said leisure (`tasks.leaning`)."""
        def look():
            try:
                kinds = {row["id"]: row["kind"] for row in self.db.query(
                    "SELECT id, json_extract(metadata, '$.kind') AS kind FROM states "
                    "WHERE json_extract(metadata, '$.kind') IN (?, ?)", (WORK, LEISURE))}
                said = {one: kind == LEISURE for one, kind in kinds.items()}
                return {task.id for task in Kept(self.db, LIVE).live()
                        if _leaning(task, said) is True}
            except Exception:    # pragma: no cover - a read, not a step
                return set()
        return set(self._answered_recently("leisure", 2.0, look) or set())

    def _how_much_is_blank(self) -> int:
        """How many tasks and pieces have no words on them, right now."""
        def count():
            try:
                return len(self._still_missing(everything=True))
            except Exception:    # pragma: no cover - a count, not the work
                return 0
        return self._answered_recently("blank", 2.0, count)

    #: How long a round waits before asking a model it could not reach
    #: again. Not two seconds, which is how often an open round is picked
    #: up: with the sign-in gone every call is refused at once, and each
    #: visit still reads every state first. Woken early when the model
    #: comes back, so this is only the longest the wait can be.
    RETRY_UNREACHABLE_AFTER = 120

    def _blocked(self, reason: str) -> None:
        """The model could not be reached on this visit."""
        self.config.set("state_round_blocked_at",
                        datetime.now(timezone.utc).isoformat(), internal=True)
        self.config.set("state_round_blocked_why", reason[:300],
                        internal=True)
        self._forget_recent_answers()

    def _not_blocked(self) -> None:
        if self.config.get("state_round_blocked_at"):
            self.config.set("state_round_blocked_at", "", internal=True)
            self.config.set("state_round_blocked_why", "", internal=True)
            self._forget_recent_answers()

    def _still_blocked(self) -> Optional[Dict[str, Any]]:
        """When the model was last unreachable, if that is recent enough to
        wait on - and when the next try is."""
        said = str(self.config.get("state_round_blocked_at", "") or "")
        if not said:
            return None
        try:
            when = datetime.fromisoformat(said)
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        again = when + timedelta(seconds=self.RETRY_UNREACHABLE_AFTER)
        if datetime.now(timezone.utc) >= again:
            return None
        return {"why": str(self.config.get("state_round_blocked_why", "")
                           or "the model could not be reached"),
                "again_said": self._their_clock(again.isoformat())}

    def _nothing_left_to_try(self, where: _Where) -> None:
        """Note that this round has had its go at everything it can.

        Not the same as finishing the work. It is the thing that stops a
        round whose answers were no use from being resumed every minute
        for ever, while leaving it honestly marked unfinished.
        """
        if where.scope == SANDBOX:
            return
        self.config.set("state_round_exhausted", True, internal=True)
        self._forget_recent_answers()
        # AND THE CLOCK STARTS HERE, not at completion.
        #
        # The hours are "how long since the agent last worked through its
        # tasks", and it did work through them - some answers were no
        # use, which is a fact about the answers. Measuring from
        # completion instead left the clock stale for ever on a round
        # that could not complete, so every look found the hours long
        # past and began another round: the same loop, one level up.
        self.config.set("state_round_completed_at",
                        datetime.now(timezone.utc).isoformat(),
                        internal=True)

    def _round_is_done(self, where: _Where) -> None:
        """Say it reached the end, which is what the six hours run from.

        AND NOTHING IS STILL WAITING FOR IT. A task is told to say
        "loading" when a round means to describe it; if the round ends
        without reaching it - interrupted, or finding nothing to read -
        that word stays on the task until some later round gets to it,
        which on a quiet task is never. The name it had is kept beside the
        marker, so the honest thing at the end of a round is to put it
        back.
        """
        if where.scope == SANDBOX:
            return
        self._let_go_of_waiting()
        self.config.set("state_round_done", True, internal=True)
        self._forget_recent_answers()
        self.config.set("state_round_completed_at",
                        datetime.now(timezone.utc).isoformat(),
                        internal=True)

    def run_if_due(self, trigger: str = "agent_heartbeat") -> Dict[str, Any]:
        """Run a round if one is due. Called when an agent heartbeat ends.

        The decision comes back whether or not it ran, so the caller can
        say why nothing happened.
        """
        verdict = self.due_now()
        if not verdict.get("run"):
            # NOT A NEW ROUND - BUT THE LAST ONE MAY HAVE STEPS LEFT. Looked
            # at on every one of these minutely checks: whatever of the last
            # round's work did not finish is carried on now, under that
            # round's cutoff, not left for the next round hours away.
            left = self._steps_left()
            if left:
                said = self._carry_on_the_steps(left)
                return dict(verdict, ran=False, carried_on=left, **said)
            self._steps_going = False
            return dict(verdict, ran=False)
        try:
            report = self.run_once(trigger=trigger)
        except Exception as exc:        # pragma: no cover - logged, not raised
            log.exception("state heartbeat round failed")
            return dict(verdict, ran=False, failed=str(exc))
        return dict(verdict, ran=True,
                    states=report.get("states_considered"),
                    tasks=report.get("tasks_found"))

    def _steps_left(self) -> List[str]:
        """What of the last finished round's work is not done, under its own
        cutoff: its stages not written up to it, stages not yet read for
        what they show about the user, a grouping of the natures left open.
        Only for the live round, and only between rounds."""
        if self.models is None or self._round_in_progress():
            return []
        try:
            held = self._round_record()
        except Exception:            # pragma: no cover - a read, not a step
            return []
        until = str(held.get("reads_until") or "")
        if not held.get("done") or not until:
            return []
        left = []
        # (What the user needs to know - Newspaper - is made
        # at the times of day they chose, by its own clock: not a step here.)
        # THE LESSONS - every kind, the work's natures among them - when
        # something is new since they were last learned.
        lessons = getattr(self, "lessons", None)
        if lessons is not None and not getattr(self, "_lessons_running", False):
            try:
                if lessons.due():
                    left.append("lessons")
            except Exception:         # pragma: no cover - a look, not a step
                log.debug("could not see whether the lessons are due", exc_info=True)
        from ...common.modules import enabled as module_on
        if self.experience is None or not self.config.get("experience_enabled", True) \
                or not module_on(self.config, "experience"):
            return left
        xp = self.experience
        try:
            # Stages are written in the Experience panel, when asked: not a
            # step a round leaves behind.
            grouping = self.config.get("experience_patterns_round") or {}
            if isinstance(grouping, dict) and grouping.get("until") and \
                    not xp.patterns_progress.get("patterns_running"):
                left.append("patterns")
        except Exception:            # pragma: no cover - a look, not a step
            log.debug("could not see what is left of the round", exc_info=True)
        return left

    def _learn_lessons(self) -> bool:
        """The lessons learned, in a thread of their own - one at a time."""
        if getattr(self, "_lessons_running", False):
            return False
        self._lessons_running = True

        def run() -> None:
            try:
                self.lessons_said = self.lessons.learn_due()
            except Exception:              # pragma: no cover - never the round's fault
                log.warning("learning the lessons failed", exc_info=True)
            finally:
                self._lessons_running = False

        import threading
        threading.Thread(target=run, name="lessons", daemon=True).start()
        return True

    def _carry_on_the_steps(self, left: List[str]) -> Dict[str, Any]:
        """One visit's worth of the last round's unfinished steps, under its
        cutoff - never beside a round, which has the same lock."""
        running = self._running_in(LIVE)
        if not running.acquire(blocking=False):
            return {}
        try:
            until = str(self._round_record().get("reads_until") or "")
            report: Dict[str, Any] = {}
            if "patterns" in left:
                report = {"patterns": {"started": self.experience.start_patterns(
                    going=lambda: self._going())}}
            if "lessons" in left:
                report["lessons"] = {"started": self._learn_lessons()}
            # Quick again only while it is getting somewhere: a step that
            # can do nothing - no model, every call refused - is looked at
            # once a minute, not every two seconds.
            self._steps_going = bool(report.get("progress"))
            self._last_steps = {"left": left, "until": until,
                                "progress": bool(report.get("progress"))}
            return {"steps": self._last_steps}
        except Exception:            # pragma: no cover - logged, not raised
            log.warning("carrying on the round's steps failed", exc_info=True)
            self._steps_going = False
            return {}
        finally:
            running.release()

    def _interval(self) -> int:
        # HOW OFTEN A ROUND HAPPENS - the one number, in hours.
        return max(60, int(round(self._every_hours() * 3600)))

    #: How often the loop WAKES to ask the gate whether a round is due.
    #: Not a setting, and not the same question as how often a round
    #: happens: the gate enforces the hours, and this is only how soon it
    #: is consulted. They were the same number for one commit, and the
    #: result was that a round interrupted by a quit sat unfinished for
    #: six hours with its tasks reading "loading" - because the only
    #: thing that would resume it was asleep for six hours. A round that
    #: wants resuming wants it now; a fresh round still waits its hours.
    LOOKS_EVERY = 60

    #: How long a visit waits before carrying on with a round it left
    #: open. Not a minute: the work is already decided and the reader is
    #: watching the half-written picture.
    CARRIES_ON_AFTER = 2

    def _looks_every(self) -> int:
        # A ROUND IN PROGRESS IS NOT A THING TO SLEEP ON. The minute is
        # how often to ask whether a NEW round is due. One already open
        # has its answer, and waiting out the poll in between visits is a
        # minute of a half-named picture for nothing.
        if self._round_in_progress():
            return self.CARRIES_ON_AFTER
        # Steps of the last round being carried on, and getting somewhere:
        # the next look is as soon as a round's next visit would be.
        if getattr(self, "_steps_going", False):
            return self.CARRIES_ON_AFTER
        return min(self.LOOKS_EVERY, self._interval())

    def _settling(self) -> int:
        """How long after start-up the first round waits.

        The delay exists so that opening the application does not cost
        the most expensive round there is while the window is still
        appearing. A round left open by the last quit is the other case:
        the work was decided before, the reader can see it unfinished,
        and making them wait a minute to watch it resume serves nobody.
        """
        if self._round_in_progress():
            return self.CARRIES_ON_AFTER
        return max(5, min(self._interval(),
                          int(self.config.get("state_heartbeat_first_delay",
                                              FIRST_DELAY_SECONDS))))

    def _how_far_through(self) -> Dict[str, Any]:
        """What this round found, what it set out to write, and how far
        it has got - read off the plan it wrote down when it opened.

        THE PLAN, NOT A RECKONING FROM THE ROWS. This used to count the
        top-level tasks as they stand - how many want describing, how
        many only a status - which is a different question every time
        it is asked, and which left out the pieces entirely. So a round
        busy writing sub-tasks showed "0 of 1 summaries written" and a
        bar that did not move, and the three numbers described nothing
        a reader could recognise.
        """
        def work_out():
            plan = self._round_plan()
            if not plan:
                return {}
            tried = set(self._tried_this_round()) | set(
                self._named_this_round())
            updated = set(self._status_this_round())
            named_pieces = self._named_pieces()
            done = 0
            for one in plan.get("summarise") or []:
                if "/" in one:
                    task_id, piece = one.split("/", 1)
                    if piece in tried or piece in named_pieces.get(
                            task_id, set()):
                        done += 1
                elif one in tried:
                    done += 1
            return {
                "found": dict(plan.get("lineage") or {}),
                "summarise": len(plan.get("summarise") or []),
                "summarised": done,
                "status": len(plan.get("status") or []),
                "status_done": sum(1 for one in plan.get("status") or []
                                   if one in updated),
            }
        out = dict(self._answered_recently("through", 2.0, work_out) or {})
        # And the experience written at the end of the round: the stages of
        # each piece of work, then the patterns and principles.
        # ALWAYS, so the stages and patterns bars are there from the start:
        # where they stand, overlaid with what is being written right now -
        # the stages while the round writes them, the patterns while their
        # thread runs.
        xp = getattr(self, "experience", None)
        if xp is not None:
            said = xp.standing()
            if xp.round_progress.get("phase") == "stages":
                said.update(xp.round_progress)
            going = xp.patterns_progress
            if going.get("patterns_running"):
                said["patterns_running"] = True
                # Each kind's own counts while its builder runs, and the
                # sorting of records into kinds that comes before them.
                for kind in ("work", "personal"):
                    mine = going.get(kind) or {}
                    if mine.get("running"):
                        said[kind] = {**(said.get(kind) or {}),
                                      **{k: mine[k] for k in
                                         ("patterns", "named") if k in mine},
                                      "running": True}
                sorting = going.get("sorting") or {}
                if sorting.get("sorting"):
                    said["sorting"] = sorting
            out["experience"] = said
        # THE STEPS AFTER THE ROUND, each its own bar: what the user needs
        # to know worked out, and every kind of lesson learned.
        people = getattr(self, "connections_round", None)
        if people is not None:
            try:
                out["connections"] = dict(people.progress or {})
            except Exception:              # pragma: no cover - a look
                pass
        lessons = getattr(self, "lessons", None)
        if lessons is not None:
            try:
                out["lessons"] = lessons.standing()
            except Exception:              # pragma: no cover - a look
                log.debug("could not see where the lessons stand", exc_info=True)
        return out

    def _named_pieces(self) -> Dict[str, set]:
        """Which pieces of each task have words on them, by piece id."""
        out: Dict[str, set] = {}
        try:
            for task in Kept(self.db, LIVE).live():
                out[task.id] = {
                    str(piece.get("id") or "") for piece in _every_piece(
                        (task.described or {}).get("pieces") or [])
                    if str(piece.get("title") or "").strip()}
        except Exception:        # pragma: no cover - a read, not a step
            pass
        return out

    def _round_plan(self) -> Dict[str, Any]:
        """What this round set out to do, if it has written it down."""
        try:
            held = json.loads(self._grouping_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if str(held.get("round") or "") != str(
                self.config.get("state_round_started_at", "")):
            return {}
        plan = held.get("plan")
        return plan if isinstance(plan, dict) else {}

    def _write_down_the_plan(self, report: Dict[str, Any]) -> None:
        """What the round found and what it will write, said once.

        Written when the round opens, because that is when it decides,
        and kept with the grouping because it goes stale at the same
        moment. A round already under way without one - begun before
        this existed - writes one from where it stands, which counts the
        work still to do rather than all of it.
        """
        if self._round_plan():
            return
        path = self._grouping_file()
        try:
            held = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        began = str(self.config.get("state_round_started_at", "") or "")
        try:
            live = list(Kept(self.db, LIVE).live())
        except Exception:        # pragma: no cover - a read, not a step
            return
        # WHAT THE ROUND FOUND: a task made in this round says how - new,
        # split from one task, merged from several - and every other task
        # is one it kept.
        lineage = {"new": 0, "split": 0, "merged": 0, "kept": 0}
        for task in live:
            made_now = began and str(task.created_at or "") >= began
            kind = str((task.born_of or {}).get("kind") or "new")
            if not made_now:
                lineage["kept"] += 1
            elif kind.startswith("split"):
                lineage["split"] += 1
            elif kind.startswith("merged"):
                lineage["merged"] += 1
            else:
                lineage["new"] += 1
        ahead = self._work_ahead(self.where(LIVE))
        summarise = list(ahead["resummarise"])
        # And every piece with no words on it, in every layer it reaches.
        by_layer = {str(one.get("task_id") or ""): one
                    for one in report.get("layers") or []}
        for task in live:
            layer = by_layer.get(task.id) or {}
            pieces = layer.get("children") or [
                _a_piece_as_drawn(one) for one in
                (task.described or {}).get("pieces") or []]
            for piece in _every_piece(pieces):
                titled = str(((piece.get("summary") or {}).get("title"))
                             or piece.get("title") or "").strip()
                if len(piece.get("states") or []) < self._min_nodes():
                    continue
                if not titled and piece.get("id"):
                    summarise.append(f"{task.id}/{piece['id']}")
        held["plan"] = {"lineage": lineage, "summarise": summarise,
                        "status": list(ahead["status"])}
        try:
            path.write_text(json.dumps(held), encoding="utf-8")
        except OSError:          # pragma: no cover - a note, not the work
            return
        self._forget_recent_answers()

    def _still_missing(self, everything: bool = False) -> List[str]:
        """Everything the saved grouping has no summarised output for.

        COMPLETENESS IS READ, NOT REMEMBERED. A round used to be finished
        because it reached the end of its own loop, which says only that
        the loop ended - a task skipped by a rule, a piece whose answer
        was unusable and a whole layer never reached all leave the same
        trace, which is none. So the question is asked of the results
        instead: does every task, and every piece inside it, have words
        on it. What is still blank is what is left to do.

        TWO CALLERS WANT TWO DIFFERENT ANSWERS, so `everything` picks.

        Asked plainly, it reports what has no words on it - the honest
        state of the work, and what the round is marked by. Three tasks
        the model would not describe is not a finished round, however
        many times they were tried.

        Asked with `everything=False`, it leaves out what this round has
        already attempted, which is what the SCHEDULE needs: a round with
        nothing left to try is not picked up again a minute later to look
        at the same three answers. Without that distinction the agent
        re-summarised the same tasks every minute, and with the two
        conflated the other way it called a round with three blanks in it
        complete.
        """
        asked = (set(self._tried_this_round()) | set(self._named_this_round())
                 if not everything else set())
        out: List[str] = []
        try:
            live = list(Kept(self.db, LIVE).live())
        except Exception:        # pragma: no cover - a read, not a step
            return out
        smallest = self._min_nodes()
        try:
            in_projects = {one for project in _Projects(self.db, LIVE).all()
                           for one in project.tasks}
        except Exception:        # pragma: no cover - a read, not a step
            in_projects = set()
        for task in live:
            held = dict(task.described or {})
            # TOO SMALL TO DESCRIBE IS NOT OWED A DESCRIPTION. The round
            # passes over a group below the smallest size worth a summary,
            # and counting it as blank anyway meant there was always
            # something "left to try" that nothing would ever try: the
            # round could not finish, was resumed every two seconds as one
            # being finished, and never read a new or a changed state
            # again. Found by a test with two states in one task.
            if len(getattr(task, "members", None) or []) < smallest \
                    or self._too_short(task):
                continue
            # A task in a project, once named, is the project's to describe.
            if task.id in in_projects and _a_real_name(task.described or {}):
                continue
            # EVERY TASK, SHOWN OR NOT.
            #
            # This skipped the groupings a summary called "not worth
            # showing", on the reasoning that words nobody reads are not
            # owed. But a round is finished when it has described what it
            # grouped, and it grouped these too - whether they are drawn
            # is a question about the page. Skipping them here meant the
            # check `continue`d before reaching their PIECES as well, so
            # a task with three blank sub-tasks under it looked complete,
            # the round had nothing left to try, and it stopped.
            if not _a_real_name(held) and task.id not in asked:
                out.append(task.id)
            # AND ITS INSIDES, by the same test - when pieces are described
            # at all. A round could end with every task named and half the
            # sub-tasks blank, and nothing noticed because the flag was
            # about the loop, not the work.
            for piece in (_every_piece(held.get("pieces") or [])
                          if self._pieces_named() else ()):
                which = str(piece.get("id") or "")
                if str(piece.get("title") or "").strip():
                    continue
                # The same for a piece: the piece loop skips one smaller
                # than this, so it is never going to be written.
                if len(piece.get("states") or []) < smallest:
                    continue
                if which and which in asked:
                    continue
                out.append(f"{task.id}/{which}")
        # EVERY PROJECT SAYS WHERE IT STANDS, like every task has a name.
        try:
            for project in _Projects(self.db, LIVE).all():
                if not project.tasks or project.described.get("status"):
                    continue
                key = f"project:{project.id}"
                if key not in asked:
                    out.append(key)
        except Exception:        # pragma: no cover - a read, not a step
            log.debug("could not read the projects", exc_info=True)
        return out

    def _status_this_round(self) -> List[str]:
        """The tasks this round has already asked where they stand."""
        try:
            held = json.loads(
                str(self.config.get("state_round_status", "[]")) or "[]")
            return [str(one) for one in held] if isinstance(held, list) else []
        except (TypeError, ValueError):
            return []

    def _also_status(self, which: str) -> None:
        """Noted beside the names, and for the same reason: a round that
        is picked up again should not pay twice for an answer it has."""
        if not which:
            return
        held = self._status_this_round()
        if which in held:
            return
        held.append(which)
        try:
            self.config.set("state_round_status", json.dumps(held),
                            internal=True)
        except Exception:        # pragma: no cover - a note, not the work
            log.debug("could not record this round's status calls",
                      exc_info=True)

    def _work_ahead(self, where: _Where) -> Dict[str, Any]:
        """What this round means to do, counted before it starts.

        TWO KINDS OF WORK AND THEY ARE NOT THE SAME SIZE. A task whose
        states have barely changed keeps the summary it has and is only
        asked where it stands now - a short call about what moved. A task
        that is half new, or has never been named at all, is described
        again from nothing. A progress line that counts them together
        says "4 of 9" for two quite different nine-task rounds.

        Counted from the rows, so it is the same answer on every visit of
        a round whose task set is already settled.
        """
        out = {"resummarise": [], "status": [], "settled": 0}
        if where.scope == SANDBOX:
            return out
        try:
            live = list(Kept(self.db, LIVE).live())
        except Exception:        # pragma: no cover - a read, not a step
            return out
        after = self._resummarise_after()
        smallest = self._min_nodes()
        try:
            in_projects = {one for project in _Projects(self.db, LIVE).all()
                           for one in project.tasks}
        except Exception:        # pragma: no cover - a read, not a step
            in_projects = set()
        for task in live:
            held = dict(task.described or {})
            if len(getattr(task, "members", None) or []) < smallest:
                continue            # never summarised, so not work ahead
            # EVERY TASK THE ROUND WILL WORK ON, which is all of them:
            # the ones not drawn are described too, last.
            if not _a_real_name(held) or wants_summary(task, after):
                out["resummarise"].append(task.id)
            elif (task.size != task.summary_members
                  or held.get("urgency") is None
                  or held.get("importance") is None
                  # The user changed its next steps: a status call is
                  # owed, to tell it.
                  or (task.id not in in_projects and _todo.pending(held)
                      and held.get("status"))):
                out["status"].append(task.id)
            else:
                out["settled"] += 1
        return out

    def _resuming_a_round(self) -> bool:
        """Whether this visit is carrying on a round somebody else began.

        Not "a round is open": the visit that OPENS one has it open too,
        and that visit is exactly the one that must do the deciding.

        The test is whether the round's GROUPING is already written down.
        That file is the opening visit's work - the merges, and the
        states each summary will quote - so its presence means the
        deciding has been done and this visit has only to finish the
        naming.

        It used to ask whether anything had been asked about yet, which
        is true of most resumed visits and not all: a round whose top
        layer was all reused and whose work is in the pieces records
        nothing in those lists, so it read every vector in the folder
        again on every visit while claiming not to be a resumption.
        """
        if not self._round_in_progress():
            return False

        def look():
            try:
                held = json.loads(
                    self._grouping_file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            return str(held.get("round") or "") == str(
                self.config.get("state_round_started_at", ""))

        # Twenty-five kilobytes of JSON off the disk, parsed, to answer
        # yes or no - and asked several times per drawing.
        return bool(self._answered_recently("resuming", 2.0, look))

    def when_next(self) -> Dict[str, Any]:
        """Whether a round is running, when the last one finished, and when
        the next regular one is due - which is the usual hours after the
        last one FINISHED, whoever started it: a round run now puts the
        next regular one off by as long."""
        done = str(self.config.get("state_round_completed_at", "") or "")
        every = self._every_hours()
        due = ""
        try:
            if done:
                finished = datetime.fromisoformat(done)
                if finished.tzinfo is None:
                    finished = finished.replace(tzinfo=timezone.utc)
                due = (finished + timedelta(hours=every)).isoformat()
        except ValueError:
            due = ""
        return {"running": bool(self._visits_in_flight.get(LIVE, 0))
                or self._round_in_progress(),
                "completed_at": done, "every_hours": every, "next_at": due}

    def start_now(self) -> Dict[str, Any]:
        """RUN NOW, from the Overview: a round begun at once, on a thread of
        its own, and answered straight away - the round takes minutes, and
        the Overview draws its progress as it goes.

        ONE ROUND AT A TIME, whoever started it. While a round is going -
        the regular one, or one run now - this does nothing and says so
        (the user asked for exactly that); and while one run now is going,
        the timer begins no other: its visits only carry that one on, and
        a visit is never two at once (`_run_visit`'s lock). An unfinished
        round the timer has given up on (`state_round_exhausted`) is not
        "going", and this runs."""
        going = bool(self._visits_in_flight.get(LIVE, 0)) or (
            self._round_in_progress()
            and not bool(self.config.get("state_round_exhausted", False)))
        if going:
            return dict(self.when_next(), started=False, running=True,
                        why="A round is already running; this one was not started.")

        def run() -> None:
            from ...common.observability import started
            try:
                # Run with the button: its calls the user's.
                with started.now(("state_heartbeat",)):
                    self.run_once(trigger="manual", scope=LIVE)
            except Exception:    # pragma: no cover - a round, not the page
                log.warning("the round started from the Overview failed", exc_info=True)

        threading.Thread(target=run, name="state-round-now", daemon=True).start()
        return dict(self.when_next(), started=True, running=True)

    def _round_in_progress(self) -> bool:
        """Whether a round has begun and not finished. A run of tasks and
        projects is one visit, never left open (section 34.5)."""
        if self._projects_scheme():
            return False
        try:
            held = self._round_record()
        except Exception:        # pragma: no cover - a read, not a step
            return False
        return bool(held.get("started_at")) and not held.get("done")

    def _loop(self) -> None:
        """Wake every interval, look, and mostly find nothing new.

        Unlike the review, there is no cheap path and no reason to hurry: a
        group that was worth summarising an hour ago is still worth it now,
        and a run that finds the same groups it found last time has spent
        its calls saying what it already said. That is why the interval is
        an hour rather than the review's quarter of one.
        """
        self._watch_the_interval()
        # THE FIRST ROUND WAITS. It used to run inside start(), which was
        # harmless while a round was a handful of states and became the
        # application hanging on launch once it was not: the first round
        # is the expensive one by a distance - the whole window, every
        # pair, and the model loaded from disk - and it was being paid
        # while the user watched a window that had not opened yet. Worse
        # on the way out: a quit during that round could not interrupt
        # it, so every heartbeat's join spent its full timeout and the
        # application took fifteen seconds to close.
        #
        # A short wait rather than the full hour, because a fresh install
        # should not have an empty panel until the top of the hour.
        if not self._sleep_until_due(self._settling):
            return
        # A ROUND LEFT UNFINISHED BY THE LAST QUIT IS THE FIRST THING
        # LOOKED FOR. The gate says whether it is a resumption; this just
        # makes sure something asks soon after start-up rather than at the
        # end of the first interval.
        while not self._stop.is_set():
            if bool(self.config.get("state_heartbeat_enabled", True)):
                self._last_run_at = time.time()
                try:
                    # THROUGH THE GATE, like every other caller.
                    #
                    # This called run_once, which is the work itself: the
                    # loop wakes every minute, so every minute it began a
                    # round regardless of the hours it was supposed to
                    # wait, the states it was supposed to need, or whether
                    # the setting was about to say no. The gate is the
                    # thing that knows all three - and it also knows the
                    # one case that should NOT wait: a round left
                    # unfinished is resumed on the next look rather than
                    # sitting half-named until its hours come round again.
                    self.run_if_due(trigger="timer")
                except Exception:
                    log.exception("state heartbeat iteration failed")
                    if self.observability is not None:
                        self.observability.record_error("state_heartbeat",
                                                        "iteration failed")
            if not self._sleep_until_due(self._looks_every):
                break

    def _watch_the_interval(self) -> None:
        """Wake this loop when its interval setting is edited, rather than on
        the next wake - which is the thing the edit was about."""
        if self.bus is None:
            return

        def changed(_topic: Any, payload: Any) -> None:
            key = (payload or {}).get("key") if isinstance(payload, dict) else None
            if key in self.WAKES_ON:
                self._interval_changed.set()

        def model_back(_topic: Any, _payload: Any) -> None:
            # SIGNED IN AGAIN, SO CARRY ON NOW. A round held up by a model
            # it could not reach waits a few minutes between tries; the
            # moment the model answers again there is nothing to wait for.
            if self.config.get("state_round_blocked_at"):
                self._not_blocked()
                self._interval_changed.set()

        try:
            self.bus.subscribe("settings.changed", changed)
            self.bus.subscribe("model.available", model_back)
            self.bus.subscribe("model.recovered", model_back)
        except Exception:                # pragma: no cover - a loop with no bus
            log.debug("could not watch the interval setting", exc_info=True)

    def _sleep_until_due(self, seconds_from_now) -> bool:
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
                return True
        return False

    # -- the round ----------------------------------------------------------
    def run_once(self, trigger: str = "timer",
                 scope: str = LIVE) -> Dict[str, Any]:
        """One visit of a round, with the embedding model held for the round.

        THE MODEL IS LOADED FOR A ROUND AND LET GO WHEN IT ENDS. A round is
        many visits - two seconds apart while it is open - so it is held
        from the first visit and released only when a visit leaves no round
        open: letting go after each visit would load it again every two
        seconds. It runs in a process of its own (see embedding.py), and
        its memory goes when it is let go.
        """
        with self._model_holds_lock:
            self._visits_in_flight[scope] = self._visits_in_flight.get(scope, 0) + 1
            if scope not in self._model_holds and self.embedder is not None:
                hold = self.embedder.loaded()
                hold.__enter__()
                self._model_holds[scope] = hold
        still_open = True
        try:
            return self._run_visit(trigger=trigger, scope=scope)
        finally:
            with self._model_holds_lock:
                self._visits_in_flight[scope] -= 1
                still_open = (str(scope) == LIVE and self._round_in_progress())
                hold = (self._model_holds.pop(scope, None)
                        if not self._visits_in_flight[scope] and not still_open
                        else None)
            if hold is not None:
                hold.__exit__(None, None, None)
            # THE ROUND DONE: every English copy still missing or stale,
            # translated (english_copies.py) - while no visit can write.
            if str(scope) == LIVE and not still_open                     and hasattr(self, "_running") and hasattr(self, "db"):
                self.fill_english_copies()

    def fill_english_copies(self, scope: str = LIVE) -> Dict[str, Any]:
        """Each task's and project's English copy, translated where it is
        missing or stale - never while a visit of the round is writing.
        Nothing when the user reads English."""
        from .english_copies import fill
        running = self._running_in(scope)
        if not running.acquire(blocking=False):
            return {"skipped": "a round is writing"}
        try:
            return fill(self.db, self.models, self.config, scope=scope)
        except Exception:
            log.warning("the English copies could not be filled", exc_info=True)
            return {"failed": True}
        finally:
            running.release()

    def _run_visit(self, trigger: str = "timer",
                   scope: str = LIVE) -> Dict[str, Any]:
        """Read every state, join them into a tree, and cut it once.

        `scope` picks where it reads and writes, and nothing else. The
        debugger passes the sandbox and gets the same algorithm over
        states somebody made up - which is the only way to see what the
        grouping does to a shape you chose, rather than what it did to
        one you were given.
        """
        # ONE LOCK PER SCOPE. There was one for both, so an hourly round
        # over the user's own states blocked every press of the debugger -
        # and the debugger did not say so: `clear_sandbox` had already
        # asked the round to stop, `run_once` returns here without
        # reaching the line that takes that back, and the rehearsal then
        # read the flag and reported that the sandbox had been emptied
        # underneath it. Two true things, neither of them the reason.
        #
        # They are separate for a better reason than the message, though:
        # a rehearsal reads made-up files in its own folder and writes
        # its own rows, and there is nothing it can collide with in the
        # real round but the lock itself.
        # From `scope`, not from `where`: the lock is taken before the
        # place is resolved, because resolving it makes the sandbox's
        # folders and an install that never opens the debugger should not
        # grow a directory it did not ask for.
        running = self._running_in(scope)
        if not running.acquire(blocking=False):
            return {"skipped": (f"a {'sandbox' if str(scope) == SANDBOX else 'real'}"
                                f" state heartbeat is already running")}
        began = time.perf_counter()
        trace_id = new_trace_id()
        where = self.where(scope)
        # A new round is not the round somebody gave up on.
        self.carry_on(where.scope)
        report: Dict[str, Any] = {
            "trace_id": trace_id,
            "trigger": trigger,
            "where": where.to_dict(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "running": True,
            # WHICH STEP, from the first second of the round.
            #
            # A round reads its states, groups them, and only then starts
            # writing names - so "summarizing now" cannot be said for the
            # whole of the part before that, and a reader watching an
            # apparently idle panel has no way to tell a long first step
            # from nothing happening at all. The step names itself.
            "stage": str((self.progress() or {}).get("stage") or ""),
            "stage_detail": str(
                (self.progress() or {}).get("stage_detail") or ""),
            "settings": {
                "interval_seconds": self._interval(),
                "stands_out": self._stands_out(),
                "least_gap": BARELY_TALLER,
                "min_nodes": self._min_nodes(),
                "most_children": ENOUGH_CHILDREN,
                "summaries": self._summaries_wanted(where),
            },
        }
        self._progress = report
        try:
            if not self._going(where):
                report["note"] = self._given_up(where)
                return self._finish(report, began)
            # TASKS AND PROJECTS (section 34.5): a run writes the new states
            # down as tasks and groups the week's tasks into projects. What
            # follows below is the scheme before it, kept for a switch that
            # is turned off.
            if self._projects_scheme():
                from .project_round import ProjectRound

                ProjectRound(self, where, report).run()
                if report.get("round_complete") or report.get("note"):
                    self._round_is_done(where)
                return self._finish(report, began)
            # What is kept decides what is read: with no tasks yet this
            # is a bounded first look, and afterwards it is everything
            # since that look. Read before the states, because the states
            # to read are not the same question on the two paths.
            self._at(report, "reading the states",
                     f"the window this round covers, in {where.name}")
            kept = Kept(self.db, where.scope)
            if where.scope == LIVE:
                self._undo_the_places_regroup(kept)
            live = kept.live()
            # THE LINE THIS ROUND READS UP TO, drawn before the first
            # state is read and kept across an interruption. A round
            # takes minutes on a busy folder, and states keep arriving
            # while it runs: without a line, the tasks summarised early
            # would be the only ones that had not seen them.
            reads_until = self._begin_round(where)
            report["reads_until"] = reads_until
            nodes, window = self._reading(where, live, kept.seen_from())
            before = len(nodes)
            nodes = _up_to(nodes, reads_until)
            if len(nodes) != before:
                report["held_back"] = before - len(nodes)
                window["why"] = (
                    str(window.get("why", ""))
                    + f" {before - len(nodes)} state(s) that arrived after "
                      f"this round drew its line are left for the next one.")
            # ROUTINE IS NOT A TASK. Meals, rest, a video, the news, a chat
            # with a friend: grouped with everything else, they made tasks
            # of their own and, worse, the piles they made took work in -
            # replayed over a labelled week, most of the states put in the
            # wrong task went into a task that was mostly routine, and
            # with the routine left out the wrong share of work fell by a
            # third. They are counted and left out of the grouping.
            routine = [node for node in nodes if node.routine]
            if routine:
                nodes = [node for node in nodes if not node.routine]
                report["routine"] = {
                    "states": len(routine),
                    "why": ("the part of a day that is no piece of work, as "
                            "the review that wrote each one said; recorded, "
                            "and never grouped into a task")}
            report["window"] = window
            report["states"] = [node.to_dict() for node in nodes]
            report["states_considered"] = len(nodes)
            if len(nodes) < 2:
                report["note"] = (
                    f"Fewer than two states in what this round reads "
                    f"({window.get('why', '')}). There is nothing to compare "
                    f"and therefore nothing to group.")
                # AND THAT IS A ROUND THAT FINISHED. Nothing to read is
                # not an interruption - there is no work left to come back
                # for - and treating it as one deadlocked the whole thing:
                # the gate saw an unfinished round, resumed it, the resumed
                # round read nothing and returned here without marking
                # itself done, and the next one did the same. Measured on
                # the live agent: a round begun at 00:59 still "being
                # resumed" with every task stuck saying "loading", because
                # the step that would have named them is below this line.
                self._round_is_done(where)
                report["round_complete"] = True
                return self._finish(report, began)

            # THE PATTERNS, BESIDE THE ROUND: they are built from the nature
            # records, which nothing in a round changes, so they need not
            # wait for it.
            if where.scope == LIVE and self.models is not None:
                try:
                    self.experience.start_patterns(
                        going=lambda: self._going(where))
                except Exception:    # pragma: no cover - never the round's
                    log.warning("could not start the patterns", exc_info=True)
            if not self._resuming_a_round():
                self._at(report, "reading what they mean",
                         f"{len(nodes)} states, cached where they have not "
                         f"changed")
            # A VISIT THAT IS ONLY FINISHING A ROUND READS NO VECTORS.
            #
            # Everything they are for was decided when the round opened:
            # the grouping is kept and picked up, the set of tasks is
            # frozen, and the states each summary will quote are chosen
            # and written down with them. What was left was arithmetic
            # whose answer the round already has - and it cost reading
            # every vector in the folder out of the database before the
            # first name of every visit.
            if self._resuming_a_round():
                vectors, source = {}, {
                    "how": "not read", "model": "",
                    "ms": 0, "read": 0,
                    "why": ("this visit is finishing a round that already "
                            "chose its grouping and its samples")}
                self._at(report, "picking up where the round left off",
                         "the grouping and the samples are already chosen")
            else:
                vectors, source = self._vectors(nodes, where)
            self._vectors_now = vectors
            if not self._going(where):
                report["note"] = self._given_up(where)
                return self._finish(report, began)
            tasks, groups = self._keep(nodes, vectors, source, report,
                                       kept, live, window, where)
            # THE ROUND HAS NOW DECIDED, so it says so - whether or not
            # it rebuilt.
            #
            # The note was written only where every pair was scored,
            # because that is the expensive thing worth keeping. But it
            # is also what tells a later visit that the deciding is done,
            # and most rounds do not rebuild: on this folder not one of
            # the last seven did. So every visit looked like the first,
            # read every vector in the folder, and paid thirty seconds to
            # load the embedding model to do it - for a round whose
            # grouping had not changed since the morning.
            if where.scope != SANDBOX:
                self._mark_the_round_decided(nodes)
            # A VISIT FINISHING A ROUND READS ITS PIECES BACK.
            #
            # The pieces of a task come from the tree, and the tree is
            # only built when a round re-derives its tasks - which a visit
            # that is only finishing one no longer does, because doing it
            # retired the rows holding everything written so far. That
            # left every layer with no children: the pieces phase had
            # nothing to walk, while the check of what is still blank
            # read the pieces off the stored rows and found five. Nothing
            # could be done and nothing was finished, so the round was
            # resumed every thirteen seconds for an evening.
            #
            # The pieces were written down as they were named. Reading
            # them back is the same tree, from the place it was kept.
            if (where.scope != SANDBOX and self._resuming_a_round()
                    and self._pieces_named()):
                self._bring_back_the_pieces(report)
            if where.scope != SANDBOX:
                self._write_down_the_plan(report)
            self._at(report, "drawing the overview",
                     "every state in the folder, by when it happened")
            report["overview"] = self._overview(
                nodes=nodes, tasks=tasks, where=where, window=window,
                processed=(report.get("continuity") or {}).get("processed")
                          or [])
            report["tasks_found"] = _count_tasks(tasks)
            report["deepest"] = _deepest(tasks)
            report["communities"] = [self._group(nodes, members, index)
                                     for index, members in enumerate(groups)]
            report["communities_found"] = len(groups)
            report["big_enough"] = sum(1 for group in report["communities"]
                                       if group["big_enough"])
            report["how"] = self._how()

            # Nothing is summarised unless it is asked for. The tree is
            # the thing being read at the moment, and a model call per
            # group would be a minute of waiting for text nobody is
            # looking at yet.
            if not self._summaries_wanted(where):
                report["summaries_written"] = []
                report["calls"] = 0
                report["tokens_in"] = 0
                report["tokens_out"] = 0
                report["note"] = ("Summaries are off. The groups below are "
                                  "the tree cut at one height; nothing was "
                                  "sent to a model.")
                # Finished, for the same reason: there is nothing further
                # this round was ever going to do.
                self._round_is_done(where)
                # AND NOTHING LEFT TO TRY, said in so many words. "Done"
                # now also asks whether anything is still blank - and with
                # summaries off, everything is - so the round read as
                # unfinished with work still to try, was resumed every two
                # seconds as a round being finished, and never read a new
                # or a changed state again.
                self._nothing_left_to_try(where)
                report["round_complete"] = True
                return self._finish(report, began)

            # THE PICTURE GOES UP NOW, before a single name is asked for.
            # The grouping is done by here and it needed no model; what
            # follows is one call per task, which is the minutes. A panel
            # that shows nothing until the last of those returns is a
            # panel that cannot be told apart from a broken one.
            # No publishing step: `_progress` IS this report object, so
            # what is written here is what the panel's next poll reads.
            report["plan"] = self._the_shape_so_far(report, nodes)
            # The store this round is writing through, so the status
            # call can keep what it learns without being handed it
            # through four call sites.
            self._kept_now = kept
            # EVERY TASK THAT IS GOING TO BE ASKED ABOUT SAYS SO FIRST,
            # before a single call is made. A round is minutes of work
            # and the panel is read while it runs; without this, a task
            # whose turn has not come shows the words it was given six
            # hours ago with nothing to say they are stale, and a task
            # being summarised right now looks identical to one that
            # finished. A task whose summary is being REUSED is not
            # waiting for anything and is left alone.
            self._say_they_are_waiting(report, kept, where)
            # AND WHOSE TURN IT IS, before the first call is made. The
            # shape goes up as soon as the grouping is done, and the first
            # model call takes seconds to answer - so for those seconds
            # the picture was complete, correct and perfectly still, which
            # reads as a round that has stalled.
            #
            # "Working" here means "this is the one being worked on",
            # which is true from the moment the queue is settled rather
            # than from the moment the answer comes back.
            # WHOSE TURN IT IS, IN THE PICTURE'S OWN NAMES.
            #
            # `waiting` holds the ids of STORED tasks - `task-live-2026...`
            # - and the picture draws layers, whose ids are positional:
            # `task_1`, `task_2`. Looking one up among the other found
            # nothing, every time, so nothing was ever marked as being
            # worked on and the picture sat still through the whole round.
            # The two id spaces have now caused this five separate ways;
            # here the fix is simply to ask the layers.
            #
            # The loop below goes in layer order, so the first layer is the
            # first turn. It is marked before any call is made, because the
            # first call is seconds long and a still picture during it
            # reads as a round that has stalled.
            if (report.get("waiting") or []) and (report.get("layers") or []):
                first = (report["layers"][0] or {}).get("id")
                if first:
                    report["summarising"] = first
                    if (report.get("plan") or {}).get("shape_only"):
                        report["plan"] = self._the_shape_so_far(report, nodes)
            # As it is sent: its language named at the top and the foot.
            report["instruction"] = SUMMARY_LANGUAGE.compose(self._instruction(),
                                                             self.config)
            report["instruction_tokens"] = estimate_tokens(
                report["instruction"])
            written = []
            by_id = {task.id: task for task in kept.live()}
            report["summaries_kept"] = []
            # THE MOST RECENTLY WORKED ON FIRST.
            #
            # A round that is interrupted leaves whatever it did not reach
            # for the next one, so the order decides which half gets done -
            # and the clustering's own order is arbitrary. Urgency would be
            # the right key if it were known, and it is not: it comes OUT
            # of the summary, so ordering by it before summarising means
            # ordering by the default, which is the same number for every
            # task and no order at all.
            #
            # What IS known before any call is when each task was last
            # worked on, and it answers nearly the same question: the thing
            # somebody was doing an hour ago is the thing they are most
            # likely to be asking about.
            when = {node.id: str(node.ends_at or node.starts_at or "")
                    for node in nodes}

            def _latest(index: int) -> tuple:
                entry = (report["layers"][index]
                         if index < len(report.get("layers") or []) else {})
                last = max((when.get(one, "") for one in
                            (entry or {}).get("states") or []), default="")
                # Descending by time, and the clustering's order breaks a
                # tie - two tasks last touched in the same minute are not
                # ordered by anything meaningful, so they keep the order
                # they had.
                return (last == "", [-ord(c) for c in last], index)

            # SEVERAL AT ONCE, WHEN THE COMPUTER HAS ROOM. Which task is next is
            # still decided here, one at a time and in the order above; the
            # calls run beside each other, as many as `self.room` allows - it
            # looks at what the user is doing before each one starts and leaves
            # them room (common/room.py) - and each answer is written down here,
            # on this thread, as it comes back. One at a time is what it does
            # when there is no room, which is what it always did.
            stopped = {"blocked": False}
            going_now: List[str] = []

            def _to_summarise():
                for index in sorted(range(len(report["communities"])),
                                    key=_latest):
                    if stopped["blocked"]:
                        return
                    group = report["communities"][index]
                    if not group["big_enough"]:
                        continue
                    # A task that gained four states is the same task and says
                    # so. Writing its summary again would cost a model call to
                    # produce the sentence it already has, and - worse - would
                    # word it differently, so a reader watching the panel would
                    # see the description of unchanged work change every round.
                    # (Said as "every round" and not "every hour": how often a
                    # round happens is a setting, and a comment that names the
                    # default goes stale the moment somebody edits it.)
                    task = by_id.get((report["layers"][index] or {}).get("task_id")
                                     if index < len(report["layers"]) else None)
                    # TOO LITTLE WORK TO BE A TASK: not shown, so not described.
                    if task is not None and where.scope != SANDBOX \
                            and self._too_short(task):
                        continue
                    # A TASK IN A PROJECT IS NOT DESCRIBED AGAIN. Only the top
                    # layer is - the project - so once a task has its name and
                    # is in one, neither its description nor its status is
                    # written again; the project's is, from its tasks' states.
                    if task is not None and where.scope != SANDBOX \
                            and self._in_a_project(task.id) \
                            and _a_real_name(task.described or {}):
                        continue
                    # A SUMMARY CAN ONLY BE REUSED IF THERE IS ONE. The
                    # turnover rule answers "has this task changed enough to
                    # be described again", which is the wrong question for a
                    # task that was never described: it has not changed, so
                    # the answer was no, so it kept a summary consisting of
                    # nothing - and the picture drew it as a circle with no
                    # name, waiting for a turn that would never come. Every
                    # task in the live folder was in that state, which is why
                    # all of them said "loading" and none said "working".
                    # A MARKER IS NOT A NAME HERE EITHER. This asked whether
                    # the title was truthy, and "loading" is - so a task
                    # carrying the marker counted as named, its empty summary
                    # was reused, and it was never asked about again. One task
                    # in the live folder stayed that way through a round that
                    # named seven others.
                    # IS THERE A SUMMARY TO REUSE? That is the question, and
                    # the answer is the summary RECORD - not the title, which
                    # is a copy kept for the page and which some tasks have
                    # never had. Asking about the title instead re-summarised
                    # every task in the rehearsal on every round, because the
                    # sandbox writes no title into the description at all.
                    #
                    # What must still refuse a reuse is the marker: a task
                    # carrying it is one that owes a description, and reusing
                    # its empty summary is how it came to owe one for ever.
                    # THE TURNOVER RULE DECIDES, as it always did - unless the
                    # task owes a description. Two attempts at this asked
                    # whether the task had a NAME, which is a copy kept for the
                    # page: the sandbox never writes one, so every rehearsal
                    # re-summarised everything, and a folder whose tasks
                    # predate titles would have done the same.
                    held_now = getattr(task, "described", {}) or {}
                    # A TASK WITH NO NAME AT ALL OWES ONE.
                    #
                    # This asked whether the title was a marker, which needed
                    # a title to be there to look at - so a task holding NO
                    # title fell through as "nothing owed" and its summary was
                    # reused for ever. On the live folder that was one task
                    # permanently blank: never asked about, so never named,
                    # and under the new ordering rule it held every piece
                    # below the top layer back behind it.
                    #
                    # The title test was standing in for scope. A sandbox
                    # rehearsal does not write names, so judging it by whether
                    # a name is there re-summarised everything every time;
                    # that is a question about the scope, and it is asked
                    # directly now.
                    owes_one = bool(held_now.get("waiting")) or (
                        where.scope != SANDBOX and not _a_real_name(held_now))
                    # AND IF THIS ROUND ALREADY NAMED IT, that name IS this
                    # round's answer: keeping it is not reusing an old summary,
                    # it is finishing the round that wrote it.
                    # ONCE PER ROUND, ANSWERED OR NOT.
                    #
                    # This asked whether the round had NAMED it, so a task
                    # whose answer came back without a name was asked again
                    # on the next visit - and again, for ever: it never gets
                    # a name, so it is never finished with, so the round
                    # never ends and the agent re-summarises the same two
                    # tasks every minute. Seen in the round record: tried
                    # ["task_2", "task_7"], named [], done False, for hours.
                    #
                    # A round gets one go at each task. If the answer was no
                    # use, the next round may try again - that is what the
                    # next round is for.
                    mine = (task is not None
                            and task.id in set(self._named_this_round()))
                    # ASKED ALREADY, IN THIS ROUND. Put ahead of everything
                    # else on purpose: "it owes a description" was decided
                    # first, and a task with no name always owes one - so
                    # recording that it had been asked changed nothing and
                    # the same task was summarised again on every visit. The
                    # round record showed it plainly: tried two, named none,
                    # done false, and six calls a minute for hours.
                    #
                    # One go per round. If what came back was no use, the
                    # next round may try again; this one is finished with it.
                    asked_already = (task is not None
                                     and task.id in set(self._tried_this_round()))
                    # CARRIED ON, NOT RE-DESCRIBED. A task the rebuild found
                    # still holding nearly all of what it held - the same
                    # work, with more of it - keeps the name it has. What
                    # moves when work continues is where it stands and how
                    # much it matters, and the status call below asks exactly
                    # that. Describing it from nothing because it GREW would
                    # give the same work a different name every time it was
                    # worked on.
                    continued = (task is not None
                                 and (getattr(task, "happened", None) or {})
                                 .get("kind") == "continues"
                                 and bool(_a_real_name(task.described or {})))
                    if (task is not None
                            and (asked_already or continued
                                 or (not owes_one
                                     and (mine or not wants_summary(
                                         task, self._resummarise_after()))))):
                        group["summary"] = {
                            "record_id": task.summary_id,
                            "kept": True,
                            "written_at": task.summary_at,
                            # How many states it described when it was written.
                            # Named `described` until the task itself gained a
                            # description, at which point one key meant a count
                            # in one branch and four paragraphs in the other,
                            # and every kept summary raised TypeError.
                            "described_states": task.summary_members,
                            # What it says, carried forward: a kept summary is
                            # still the summary this task has, and a page that
                            # blanks its description on the rounds it was not
                            # rewritten is a page that flickers.
                            "described": dict(task.described),
                            # The words it already has. Without these the page
                            # drew a task with no name on every round its
                            # summary was reused, which is most rounds.
                            # The name it has, and not the marker: publishing
                            # that as this round's title put the word straight
                            # back into the picture.
                            "title": _a_real_name(task.described),
                            "urgency": int(task.described.get("urgency", 4) or 4),
                            "importance": int(
                                task.described.get("importance", 4) or 4),
                            "weighed_why": str(
                                task.described.get("weighed_why") or ""),
                            "summary": str(task.described.get("summary") or ""),
                            "why": (f"Written when the task held "
                                    f"{task.summary_members} states and it holds "
                                    f"{task.size} now, which is less than the "
                                    f"{self._resummarise_after():.0%} turnover it "
                                    f"would take to be a different claim."),
                        }
                        report["summaries_kept"].append(task.id)
                        # NO FULL SUMMARY, AND STILL AN ANSWER TO "WHERE DOES
                        # THIS STAND". The turnover rule is right about the
                        # other three parts - where the work lives and what it
                        # is in aid of do not change because four states
                        # arrived - and wrong about this one, whose whole job
                        # is to move. Under that rule alone it sat still for
                        # hours while the work it described went on happening.
                        #
                        # The question is built here and the call handed out
                        # like a summary's, so it runs beside the others
                        # rather than holding up the loop - see
                        # `_status_to_ask`. Labelled now with what it had, and
                        # again when the answer lands.
                        asking = self._status_to_ask(report, task, index,
                                                     nodes, where)
                        self._label(report, index, group["summary"])
                        if asking is None:
                            continue
                        marked = str(report.get("summarising") or "")
                        going_now.append(marked)
                        report["summarising_all"] = list(going_now)
                        yield index, group, task, marked, asking
                        continue
                    if not self._going(where):
                        report["note"] = self._given_up(where)
                        return
                    # What this task was last described as, so the model can
                    # amend a part rather than write four again.
                    if task is not None and task.described:
                        group["described"] = dict(task.described)
                    self._at(report, "summarising",
                             f"task {index + 1} of {len(report['communities'])}, "
                             f"{group['size']} states")
                    # WHICH ONE IS BEING WORKED ON, not just that something
                    # is. Every task awaiting its turn says "loading", and a
                    # reader watching wants to know which of them is the one
                    # actually costing the time - "all of them are loading"
                    # is true and useless.
                    # THE LAYER'S ID, NOT THE GROUP'S. They are different
                    # names for the same work - `group_1` is what the
                    # clustering called it and `task_1` is what the picture
                    # draws - and comparing the wrong one meant nothing ever
                    # matched, so every circle said "loading" and none ever
                    # said "working".
                    report["summarising"] = (
                        (report["layers"][index] or {}).get("id")
                        if index < len(report.get("layers") or []) else
                        group.get("id") or "")
                    if (report.get("plan") or {}).get("shape_only"):
                        report["plan"] = self._the_shape_so_far(report, nodes)
                    # ASKED, whatever comes back. Recorded before the call,
                    # because a call that fails is still a call this round has
                    # spent on this task - and the thing waiting on this list
                    # is the rule that nothing below the top layer starts
                    # until the top layer has been gone through.
                    # BY THE STORED ID, like the names are. The positional
                    # one - `task_2` - is this round's word for whatever is
                    # second, and nothing outside the round means the same
                    # thing by it.
                    going_now.append(str(report.get("summarising") or ""))
                    report["summarising_all"] = list(going_now)
                    # ITS NEXT STEPS, when nothing above it holds it: a task
                    # in no project is the top of what the user sees, and
                    # the call that describes it writes its list as well.
                    group.pop("todo", None)
                    if task is not None and not self._in_a_project(task.id):
                        shown = self._todo_asking("state_tasks", where.scope,
                                                  task.id)
                        if shown is not None:
                            group["todo"] = shown
                    yield index, group, task, report.get("summarising"), None

            def _summarise_one(job):
                _index, group, task, _marked, asking = job
                if asking is not None:
                    return self._status_asked(report, task, asking)
                return self._summarise(nodes, group, trace_id, where,
                                       vectors, report)

            def _land(job, summary, error):
                index, group, task, marked, asking = job
                if marked in going_now:
                    going_now.remove(marked)
                report["summarising_all"] = list(going_now)
                if error is not None:
                    raise error
                if asking is not None:
                    # A status answer: kept as `_bring_status_up_to_date`
                    # keeps one, and never a reason to stop the others.
                    self._keep_status(report, task, group["summary"],
                                      summary, where)
                    self._label(report, index, group["summary"])
                    return True
                # RECORDED AFTER THE CALL, AND ONLY IF IT GOT THROUGH.
                #
                # It was written down before the call, so a call that never
                # reached the model still counted as this round's one go.
                # With the sign-in expired every call failed at once: each
                # visit asked one task, was refused, stopped - and left the
                # task marked as tried. Twenty-nine visits later everything
                # was "tried", nothing was named, and the round settled in
                # to wait six hours. A refused call is not an attempt; the
                # model never saw the question.
                if where.scope != SANDBOX and not summary.get("blocked"):
                    self._also_tried(str(getattr(task, "id", "") or (
                        (report["layers"][index] or {}).get("id")
                        if index < len(report.get("layers") or []) else
                        group.get("id") or "")))
                group["summary"] = summary
                self._spent(report, summary)
                self._label(report, index, summary)
                # ONE TASK FURTHER ON, in the picture, now. The shape was
                # drawn before any of these calls; this is the name
                # landing on the circle that was waiting for it, so a
                # reader watching sees the tree fill in rather than a
                # drawing that sits unchanged for minutes and then
                # arrives complete.
                if (report.get("plan") or {}).get("shape_only"):
                    report["plan"] = self._the_shape_so_far(report, nodes)
                if task is not None and summary.get("record_id"):
                    # THE VERSION IT REPLACES, kept on the task: what it was
                    # called, when, how big it was then, and why it was
                    # described again - so the page can show what this task
                    # was before, not only what it is now.
                    earlier = _version_of(task, summary["record_id"])
                    task.summary_id = summary["record_id"]
                    # What drift is measured from now: the name describes
                    # the task as it is at this moment.
                    task.named()
                    task.summary_at = task_now()
                    task.summary_members = task.size
                    # MERGED, not replaced. A part the model did not send
                    # is a part it had nothing new to say about, and the
                    # one it already has is still the best thing known.
                    held = dict(task.described)
                    held.update(summary.get("described") or {})
                    # AND THE TITLE AND THE SENTENCE, kept beside the four
                    # parts. They lived only in the library record, so a
                    # round that reused a summary had no way to say what
                    # that summary was: the kept branch built a summary
                    # dict with a record id and no title, and the task
                    # came out of round two with its name blank although
                    # nothing about it had changed. The record is still
                    # where the summary is published; this is the copy
                    # the page reads.
                    # IT IS NOT WAITING ANY MORE. The marker is a note
                    # that a description is owed, and this is the moment
                    # it stops being owed - so it is cleared HERE, where
                    # the description arrives, rather than only at the end
                    # of a round. Clearing it only at the end meant a
                    # scope that never reaches that step kept the mark for
                    # ever, and every later round re-wrote every task
                    # because every task still said it owed one.
                    held.pop("waiting", None)
                    held.pop("waiting_size", None)
                    held.pop("was", None)
                    if summary.get("title"):
                        held["title"] = summary["title"]
                    if summary.get("summary"):
                        held["summary"] = summary["summary"]
                    if earlier:
                        held["history"] = ([earlier] + list(
                            held.get("history") or []))[:HISTORY_KEPT]
                    # AND WHETHER IT IS SHOWN AT ALL. The model decides
                    # that a task is a way of working rather than a thing
                    # with an end, and the decision has to outlive the
                    # round: without it a restart draws every one of them
                    # again, including the ones it had already judged not
                    # worth drawing.
                    held["concrete"] = bool(summary.get("concrete", True))
                    if summary.get("concrete_why"):
                        held["concrete_why"] = summary["concrete_why"]
                    # Asked with the question that can take a task out.
                    held["asked_for_buried"] = True
                    # And asked to judge it afresh, which every answer now
                    # does.
                    held["concrete_asked"] = True
                    # ALWAYS KEPT, INCLUDING THE 4. A task the model
                    # did not put a number on is not an unweighed task -
                    # the instruction says to answer 4 where the states
                    # give no sign, so 4 IS the answer and the panel can
                    # say where it came from. Keeping it only when the
                    # model volunteered a number left a task that had
                    # just been summarised reading "not weighed yet",
                    # which is a different and untrue claim.
                    for name in ("urgency", "importance"):
                        held[name] = summary.get(name, 4)
                    held["weighed_how"] = (
                        "read" if (summary.get("urgency_given")
                                   and summary.get("importance_given"))
                        else "default")
                    if summary.get("weighed_why"):
                        held["weighed_why"] = summary["weighed_why"]
                    if summary.get("written_by"):
                        held["written_by"] = summary["written_by"]
                    task.described = held
                    summary["described"] = held
                    kept.save([task])
                    # Noted before anything can interrupt: a name written
                    # and not recorded is a name that gets written again.
                    if where.scope != SANDBOX:
                        self._also_named(task.id)
                    if summary.get("buried") and task.profiled:
                        self._take_out_what_is_buried(
                            task, summary["buried"], kept, where, report)
                    if summary.get("not_this_work") and task.profiled:
                        self._take_out_other_work(task, summary["not_this_work"],
                                                  where, report, "summary")
                # ITS NEXT STEPS. Cleared of what the call was shown as soon
                # as the call was answered - with a name or without one,
                # with a list or without one - and kept whole when it never
                # reached the model.
                if task is not None and group.get("todo") is not None:
                    self._land_todo(
                        "state_tasks", where.scope, task, group.pop("todo"),
                        summary.get("todo"),
                        str(summary.get("todo_note") or ""),
                        failed=bool(summary.get("blocked"))
                        or not summary.get("calls"))
                    if isinstance(summary.get("described"), dict):
                        summary["described"].update(
                            _todo.drawn(task.described))
                if summary.get("blocked"):
                    report["blocked"] = True
                    report["reason"] = summary["blocked"]
                    stopped["blocked"] = True
                    return False
                if summary.get("record_id"):
                    written.append({"record_id": summary["record_id"],
                                    "title": summary.get("title", ""),
                                    "members": group["size"]})
                return True

            at_once(_to_summarise(), _summarise_one, self.room,
                    going=lambda: self._going(where), landed=_land,
                    pool=self.pool, kind="grouping",
                    told=lambda running, allowed, why: self._say_at_once(
                        report, running, allowed, why))
            report["summarising_all"] = []

            # A top task that is a state may have a real task inside it,
            # so the ones that were not shown are opened and their
            # sub-tasks asked the same question. Only those: a task that
            # is already shown needs nothing underneath it, and asking
            # anyway is a model call per sub-task for nothing.
            # WHAT THE USER WILL SEE IS SUMMARISED FIRST, as far as that
            # can be known - and it cannot be known in advance. Whether a
            # task is shown at all is `concrete`, and `concrete` comes OUT
            # of its summary: the model is what decides that "the user
            # keeps checking the same three sites" is a way of working
            # rather than a thing with an end. So the order cannot be
            # "displayed tasks first" from the start; the most it can be,
            # and now is:
            #
            #   1. every top task, which is what makes `concrete` known
            #   2. the pieces of the tasks that ARE shown, by urgency
            #   3. only then, the hunt inside the ones that are NOT
            #
            # Step 3 is a search rather than a description - it opens a
            # task the model called a way of working, in case something
            # with an end is hiding inside it - and nothing on the page
            # waits for it. It used to run second, so a reader watching
            # saw calls being spent on tasks they would never be shown
            # while the ones in front of them still said "loading".
            # ONE TOP TASK AT A TIME, THE MOST URGENT FIRST. Every task
            # above has a name by now - the top layer is finished before
            # anything below it begins - so a reader already has the
            # whole picture and is watching it fill in. Doing that in
            # urgency order means an interrupted round leaves the pieces
            # of the work that needs doing soonest, rather than the
            # pieces of whichever task happened to be first in the list.
            shown = report.get("layers") or []
            # AND NOT BEFORE THE TOP LAYER IS DONE.
            #
            # The comment above says the top layer is finished by now, and
            # it was not: a visit stops when its budget runs out or the
            # agent asks it to, which can land in the middle of the first
            # loop - and the second loop then ran anyway, spending the
            # next visit's calls on the sub-tasks of ONE task while four
            # others at the top still had no name at all. Seen on the live
            # folder: every piece of one conversation written up while the
            # tasks beside it were still blank.
            #
            # The rule is the reader's: the whole top layer first, then
            # each task's pieces in urgency order. So if anything up there
            # is still unnamed, this visit has nothing to do down here -
            # the next one carries on where it left off.
            asked = set(self._tried_this_round())
            groups_now = report.get("communities") or []
            try:
                by_stored = {task.id: task
                             for task in Kept(self.db, where.scope).live()}
            except Exception:    # pragma: no cover - a read, not a step
                by_stored = {}

            def _still_coming(index: int, one: Dict[str, Any]) -> bool:
                """Whether the top-layer loop is still going to name this.

                Only what that loop would ACTUALLY reach counts. It passes
                over a group too small to be worth a summary, so such a
                task never gets a name - and a rule that waits for every
                task to be named waits for that one for ever. Measured on
                the live folder: one group below the threshold, and three
                visits in a row that asked about nothing and held every
                piece back anyway.
                """
                if not (one.get("summary") or {}).get("concrete", True):
                    return False
                if str((one.get("summary") or {}).get("title") or "").strip():
                    return False
                # BY THE STORED ID. `one["id"]` is this round's word for
                # whatever is in that position - `task_1` - and the list
                # of what has been asked about is keyed by the id the
                # task keeps between rounds. Comparing the two never
                # matches, so every task looked unasked and the pieces
                # were held back for ever: the round could not finish,
                # and a round that cannot finish is resumed every minute.
                if str(one.get("task_id") or "") in asked:
                    return False
                # NOR ONE WITH TOO LITTLE WORK IN IT. The loop passes over
                # those too, so waiting for its name held the round open
                # for good: two tasks under two hours, a visit every
                # eighteen seconds, for most of an afternoon.
                held = by_stored.get(str(one.get("task_id") or ""))
                if held is not None and where.scope != SANDBOX \
                        and (self._too_short(held)
                             or (self._in_a_project(held.id)
                                 and _a_real_name(held.described or {}))):
                    return False
                group = (groups_now[index] if index < len(groups_now) else {})
                return bool((group or {}).get("big_enough"))

            unnamed = [one.get("id") for index, one in enumerate(shown)
                       if _still_coming(index, one)]
            if unnamed:
                report["pieces_held_back"] = {
                    "why": ("The top layer is not finished, and nothing "
                            "below it is written until it is."),
                    "still_unnamed": unnamed,
                }
                log.info("not summarising any pieces yet: %d top-layer "
                         "task(s) still have no name - the round stays "
                         "open and the next look carries on", len(unnamed))
                shown = []
            # DRAWN FIRST, THEN THE REST - each in urgency order.
            #
            # A grouping the summary rejected still gets its insides
            # described: the round grouped it, so the round owes it
            # words, and a round that leaves them out can never call
            # itself finished. But it goes last, because a reader
            # watching the picture fill in is watching the tasks they can
            # see, and an interrupted round should have spent its calls
            # on those.
            def _in_turn():
                order = self._by_urgency(report)
                for drawn_first in (True, False):
                    for index in order:
                        if not 0 <= index < len(shown):
                            continue
                        is_drawn = bool((shown[index].get("summary") or {})
                                        .get("concrete", True))
                        if is_drawn is drawn_first:
                            yield index

            for index in (_in_turn() if self._pieces_named() else ()):
                task = shown[index]
                # A PIECE OF WHAT? The top layer is written first so that
                # a reader has the whole picture before it fills in, and
                # a task whose own description came back unusable has no
                # place in that picture yet - so writing up its insides
                # puts named sub-tasks under a parent that is still
                # blank. The panel said it plainly: "summarizing now: a
                # task that has no name yet, in one of its sub-tasks".
                #
                # The order depends on it too. Pieces are done one task
                # at a time, most urgent first, and the urgency comes OUT
                # of the task's summary - so a task without one cannot be
                # placed in that order at all.
                #
                # Its pieces wait for the next round, which will have
                # another go at the task itself first.
                if not str((task.get("summary") or {}).get("title")
                           or "").strip():
                    report.setdefault("pieces_skipped", []).append(
                        task.get("id"))
                    continue
                if len(task.get("children") or []) < 2:
                    continue
                if not self._going(where):
                    break
                self._name_the_pieces(report, task, nodes, trace_id, where,
                                      by_id.get(task.get("task_id")))
                task["plan"] = self._plan(task, nodes, trace_id, where, report)
                # Kept as soon as they are named, not at the end of the
                # round: a round interrupted after naming three tasks has
                # three tasks' worth of insides worth keeping.
                self._keep_the_pieces(
                    task, by_id.get(task.get("task_id")))
                report.setdefault("filled_in", []).append(task.get("id"))
            # THE TOP LAYER IS FINISHED BEFORE ANYTHING BELOW IT STARTS,
            # and then the insides are done one top task at a time, the
            # most urgent first.
            #
            # Both halves of that matter for a reader watching it happen.
            # Finishing the top layer first means the picture has every
            # task in it, named, within the first few calls - a tree that
            # grows a branch at a time tells you nothing until it is
            # done. Going by urgency after that means the sub-tasks that
            # appear first are the ones under the work that needs doing
            # soonest, so an interrupted round leaves the most useful
            # half finished rather than an arbitrary one.
            #
            # Urgency is known by here because the top layer wrote it.
            #
            # AND THE PROJECTS BETWEEN THE TWO: a project's status is made
            # from its tasks' statuses, so it comes after them - and before
            # any task's pieces, so the top of the picture is complete
            # first.
            if where.scope != SANDBOX or self._summaries_wanted(where):
                self._bring_projects_up_to_date(report, where)
            if self._pieces_named():
                self._summarise_inside(report, nodes, trace_id, where,
                                       order=self._by_urgency(report))
            # And for each task that IS shown, what its pieces are and in
            # what order: the last layer is drawn as a tech tree, which
            # needs a name per piece and the edges between them.
            # And the tasks themselves, once, after they all have names:
            # what is running now, when each of it started, and which of
            # it waits on something else.
            if self._going(where):
                report["plan"] = self._plan_the_lot(report, nodes,
                                                    trace_id, where)
            # NO STAGES HERE. Turning the tasks into stages is done in the
            # Experience panel, when the user asks for it - not by a round.
            # THE END, said only here. Everything above can be
            # interrupted - a quit, a crash, a model that stops
            # answering - and a round that did not reach this line is
            # resumed rather than waiting its six hours, because the
            # tasks it never got to are still showing 'loading'.
            # A ROUND THAT PUT WORK OFF IS NOT FINISHED.
            #
            # The pieces are held back while any top-layer task still has
            # no name, so that the whole top layer is written before
            # anything below it. Marking the round done in the same breath
            # threw that work away rather than deferring it: the pieces
            # stayed "preparing", nothing was running, and the next round
            # was six hours off. Left open, the next look carries on - and
            # by then the top layer is named, so it goes straight to them.
            held_back = bool(report.get("pieces_held_back"))
            if where.scope != SANDBOX:
                if report.get("blocked"):
                    self._blocked(str(report.get("reason") or ""))
                else:
                    self._not_blocked()
            # READ BACK BEFORE DECLARING IT DONE. The loop reaching its
            # end is not the same as the work being there.
            # What has no words on it, counting what was tried and failed.
            missing = (self._still_missing(everything=True)
                       if where.scope != SANDBOX else [])
            # What this round could still have a go at - which is nothing
            # when it cannot write at all. With summaries switched off, or
            # no model to ask, no task is ever tried, so "left to try"
            # never emptied: the round stayed open, was picked up every two
            # seconds as a round being finished, and so never read a new
            # state or a changed one again.
            can_write = (self._summaries_wanted(where)
                         and self.models is not None)
            left = (self._still_missing()
                    if where.scope != SANDBOX and can_write else [])
            if missing:
                report["still_missing"] = missing[:20]
                report["still_missing_count"] = len(missing)
                log.info("round stays open: %d task(s) or piece(s) have no "
                         "summary yet", len(missing))
            # TWO FACTS, AND THEY WERE ONE FLAG.
            #
            # "Everything this round meant to do has words on it" is
            # about the WORK, and it is what a reader is told. "There is
            # nothing left this round can try" is about the SCHEDULE, and
            # it is what stops the agent picking the same round up every
            # minute to do nothing. A round where the model gave three
            # unusable answers is the second without being the first, and
            # calling it complete said the work was there when it was
            # not.
            # The stages are not part of the round: they are written in the
            # Experience panel, when asked.
            if self._going(where) and not held_back and not left:
                self._nothing_left_to_try(where)
                if not missing:
                    self._round_is_done(where)
                    report["round_complete"] = True
                # NOTHING IS BEING WORKED ON NOW. Left set, the last task
                # named would pulse for the next six hours, which says
                # something untrue in the one place a reader looks to find
                # out whether anything is happening.
                report["summarising"] = ""
                # Cleared on whichever drawing is in place. By here the
                # early shape has usually been replaced by the ordered
                # plan, and rebuilding the shape would not touch that one
                # - so the last task named went on pulsing after the round
                # had finished, for as long as the next one took to start.
                for node in ((report.get("plan") or {}).get("nodes") or []):
                    node["working"] = False
                if (report.get("plan") or {}).get("shape_only"):
                    report["plan"] = self._the_shape_so_far(report, nodes)
            report["summaries_written"] = written
            report["display"] = self._shown(report, nodes)
            # Which of them the user would be shown, and which are the
            # application's own notes. Reported because the difference is
            # a judgement the model made and is worth checking.
            report["not_shown"] = [
                {"task": ((report.get("layers") or [{}])[index]
                          if index < len(report.get("layers") or [])
                          else {}).get("id") or group.get("id"),
                 "title": (group.get("summary") or {}).get("title", ""),
                 "why": (group.get("summary") or {}).get("concrete_why", "")}
                for index, group in enumerate(report["communities"])
                if group.get("summary")
                and not (group["summary"] or {}).get("concrete", True)]
            # What each summary cost, named by the task it was for. The
            # totals below are the same numbers added up; these are the
            # ones that say which task is expensive.
            report["spent"] = {
                "per_task": [
                    # Named by the task the page shows, not by the
                    # community underneath it: they are one grouping in
                    # one order, and filing this under the other name
                    # meant the page could never find it.
                    {"task": ((report.get("layers") or [{}])[index]
                              if index < len(report.get("layers") or [])
                              else {}).get("id") or group.get("id"),
                     "group": group.get("id"),
                     "states": group.get("size", 0),
                     "kept": bool((group.get("summary") or {}).get("kept")),
                     "concrete": bool((group.get("summary") or {}).get(
                         "concrete", True)),
                     "searched": bool((group.get("summary") or {}).get(
                         "searched")),
                     "searches": int((group.get("summary") or {}).get(
                         "searches", 0) or 0),
                     "calls": int((group.get("summary") or {}).get("calls", 0)),
                     "prompt_tokens": int((group.get("summary") or {}).get(
                         "prompt_tokens", 0) or 0),
                     "sent_tokens": int((group.get("summary") or {}).get(
                         "sent_tokens", 0) or 0),
                     "tokens_in": int((group.get("summary") or {}).get(
                         "tokens_in", 0) or 0),
                     "tokens_out": int((group.get("summary") or {}).get(
                         "tokens_out", 0) or 0),
                     "tokens_cached": int((group.get("summary") or {}).get(
                         "tokens_cached", 0) or 0),
                     "tokens_new": int((group.get("summary") or {}).get(
                         "tokens_new", 0) or 0),
                     "ms": int((group.get("summary") or {}).get("ms", 0) or 0)}
                    for index, group in enumerate(report["communities"])
                    if group.get("summary")],
                "instruction_tokens": report.get("instruction_tokens", 0),
                "why": ("The instruction is sent again with every summary, "
                        "so what a call costs to send is the states plus "
                        "the instruction - which is why the two are "
                        "counted apart."),
            }
            report["calls"] = sum(int((group.get("summary") or {}).get("calls") or 0)
                                  for group in report["communities"])
            report["tokens_in"] = sum(
                int((group.get("summary") or {}).get("tokens_in") or 0)
                for group in report["communities"])
            report["tokens_out"] = sum(
                int((group.get("summary") or {}).get("tokens_out") or 0)
                for group in report["communities"])
            return self._finish(report, began)
        except Exception as exc:         # pragma: no cover - reported, not raised
            log.exception("state heartbeat failed")
            report["error"] = str(exc)[:400]
            return self._finish(report, began)
        finally:
            running.release()

    def _finish(self, report: Dict[str, Any], began: float) -> Dict[str, Any]:
        report["running"] = False
        stages = report.get("stages") or []
        if stages:
            stages[-1]["seconds"] = round(
                time.perf_counter() - stages[-1]["began"], 2)
        report["stage"] = "done"
        report["stage_detail"] = ""
        report["duration_ms"] = int((time.perf_counter() - began) * 1000)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        # A sandbox round is kept apart from the real one. It must not
        # become `last_report`: that is what the status bar counts tasks
        # from and what the panel shows, and made-up work standing in for
        # the user's own would be a lie told by a test.
        if (report.get("where") or {}).get("scope") == SANDBOX:
            self._sandbox_report = report
            return report
        self._last_report = report
        self._progress = report
        self._keep_the_drawing(report)
        self._add_what_this_visit_spent(report)
        if self.bus is not None:
            try:
                self.bus.publish("state_heartbeat.finished",
                                 {"communities": report.get("communities_found", 0),
                                  "written": len(report.get("summaries_written") or [])})
            except Exception:            # pragma: no cover - a bus that refused
                log.debug("could not publish the state heartbeat", exc_info=True)
        return report

    # -- the three parts ----------------------------------------------------
    # -- the limits, each with its measurement in tasks.py ------------------
    def _in_a_project(self, task_id: str) -> bool:
        """Whether a task is in a project - and so not at the top itself."""
        known = getattr(self, "_project_of", None)
        if not known:
            try:
                known = {one: project.id for project in
                         _Projects(self.db, LIVE).all() for one in project.tasks}
            except Exception:    # pragma: no cover - a read, not a step
                known = {}
        return task_id in known

    # -- the next steps on what is at the top ------------------------------
    def change_todo(self, which: str, add: Any = None, remove: Any = None,
                    finish: Any = None, unfinish: Any = None) -> Dict[str, Any]:
        """The user adds a step to the next steps of a project or of a task
        in no project, deletes one, or marks one finished - shown at once,
        and told to the next call that describes it.

        Only what is at the top has a list: a task inside a project is a
        part of something the project's list already covers. Live or
        sandbox, whichever the id belongs to. LookupError for an id that
        is neither, or a step that is not there; ValueError for a task in
        a project, or nothing to do.
        """
        which = str(which or "").strip()
        found = None
        for scope in (LIVE, SANDBOX):
            projects = _Projects(self.db, scope).all()
            if any(project.id == which for project in projects):
                found = ("state_projects", scope, "project")
                break
            task = next(iter(Kept(self.db, scope).by_ids([which])), None)
            if task is not None and not task.retired_at:
                if any(which in project.tasks for project in projects):
                    raise ValueError("this task is part of a project - its "
                                     "next steps are the project's")
                found = ("state_tasks", scope, "task")
                break
        if found is None:
            raise LookupError("no such project or task")
        table, scope, kind = found
        adding, removing = str(add or "").strip(), str(remove or "").strip()
        finishing = str(finish or "").strip()
        unfinishing = str(unfinish or "").strip()
        if not adding and not removing and not finishing and not unfinishing:
            raise ValueError("say what to add, or which step to remove or "
                             "mark finished")

        def how(described: Dict[str, Any]) -> Dict[str, Any]:
            if adding:
                return _todo.added(described, adding)
            if removing:
                return _todo.removed(described, removing)
            if unfinishing:
                return _todo.unfinished(described, unfinishing)
            return _todo.finished(described, finishing)

        written = _todo.change(self.db, table, scope, which, how)
        if written is None:
            raise LookupError("no such project or task")
        return {"id": which, "kind": kind, "scope": scope,
                **_todo.drawn(written)}

    def _todo_stored(self, table: str, scope: str,
                     which: str) -> Dict[str, Any]:
        """A row's description as it is now, not as a round read it."""
        try:
            row = self.db.query_one(f"SELECT described FROM {table} "
                                    f"WHERE id = ? AND scope = ?",
                                    (which, scope))
            said = json.loads((row or {}).get("described") or "{}")
        except Exception:        # pragma: no cover - a read, not a step
            return {}
        return said if isinstance(said, dict) else {}

    def _todo_due(self, table: str, scope: str, which: str) -> bool:
        """Whether the user changed its next steps since a call saw them.

        Read from the row: the round's copy was read when it began, and
        the edit may have been made a minute ago."""
        if self.db is None:
            return False
        return _todo.pending(self._todo_stored(table, scope, which))

    def _todo_asking(self, table: str, scope: str,
                     which: str) -> Optional[Dict[str, Any]]:
        """The list and the user's changes, about to be shown to a call -
        and noted on the row as being shown, so a step the user deletes
        while the call is out is known to be news to the next one."""
        shown: Dict[str, Any] = {}

        def how(described: Dict[str, Any]) -> Dict[str, Any]:
            shown.update(_todo.snapshot(described))
            return {"todo_asking": {
                **{kind: list(shown[kind]) for kind in _todo.CHANGES},
                "at": shown["at"]}}

        try:
            if _todo.change(self.db, table, scope, which, how) is None:
                return None
        except Exception:        # pragma: no cover - a note, not the work
            log.debug("could not note the next steps being asked about",
                      exc_info=True)
            return None
        return shown

    def _land_todo(self, table: str, scope: str, thing: Any,
                   shown: Optional[Dict[str, Any]],
                   said: Optional[List[str]], note: str = "",
                   failed: bool = False) -> None:
        """The new list, from what a call was shown and what it said, on
        the row and on the copy the round holds."""
        if shown is None or thing is None:
            return
        try:
            written = _todo.change(
                self.db, table, scope, str(getattr(thing, "id", "") or ""),
                lambda described: _todo.answered(described, shown, said,
                                                 note, failed))
        except Exception:        # pragma: no cover - a list, not the round
            log.debug("could not keep the next steps", exc_info=True)
            return
        if written is None:
            return
        held = dict(getattr(thing, "described", None) or {})
        for key in _todo.KEYS:
            if key in written:
                held[key] = written[key]
            else:
                held.pop(key, None)
        thing.described = held

    def _pieces_named(self) -> bool:
        """Whether a task's pieces are described and drawn: never.

        ONLY THE TOP LAYER IS DESCRIBED - tasks, and projects above them -
        and nothing under a task is unfolded on the page. Naming pieces cost
        a model call per piece every round - they had no lasting identity, so every round
        found them afresh and named them all again. A task's pieces are
        still worked out, without a model, because their centres are how
        a state about one of them finds the task.
        """
        return False

    def _deepest_layers(self) -> int:
        return max(1, min(6, int(self.config.get("state_task_deepest_layers",
                                                 DEEPEST_LAYERS))))

    def _first_round_days(self) -> int:
        return max(1, int(self.config.get("state_first_round_days",
                                          FIRST_ROUND_DAYS)))

    def _max_states(self, where: Optional[_Where] = None) -> int:
        """How many states a round reads, which is not the same in both.

        The real window is a thousand because that is how much of the
        calendar the grouping should cover. The sandbox's is smaller
        because its whole purpose is to be watched: a rehearsal over a
        thousand states is three rounds of clustering and a model call per
        task, and a person pressing a button wants to see the end of it.
        A smaller window changes nothing about what the code does - the
        same rounds, the same signals, the same arithmetic - only how long
        it takes to say so.
        """
        if where is not None and where.scope == SANDBOX:
            return max(2, int(self.config.get("state_sandbox_max_states",
                                              SANDBOX_STATES_PER_ROUND)))
        return max(2, int(self.config.get("state_heartbeat_max_states",
                                          MAX_STATES_PER_ROUND)))

    def _instruction(self) -> str:
        """The summary prompt, in the language the user reads the app in.

        The states themselves are written in English, because one small
        model has to read all of them and an English-only model is a
        fifth the size of a multilingual one of the same quality. That is
        a decision about what the MACHINE reads. A summary is what the
        USER reads, and a model given English states will answer in
        English unless told otherwise - so the user would be left reading
        half their agent in a language they did not choose.
        """
        # Named at the top as well as at the foot. One line after sixteen
        # thousand characters of English prose and English worked
        # examples is a rule the examples outvote: a model shown
        # twenty GOOD summaries in English will write English,
        # whatever the last sentence says. `replace` and not
        # `format`, because the prompt is full of JSON braces.
        # Its English copy asked for only when the user does not read English.
        return SUMMARY_PROMPT.replace(
            "{in_english}", SUMMARY_ENGLISH if needs_english_copy(self.config) else "")

    def _shape(self) -> Dict[str, Any]:
        """The settings that decide how the tree is read as tasks.

        Not the ones that decide what is appended: those take effect on
        the next state either way. These four only matter when a tree is
        being read, so changing one does nothing at all until a rebuild -
        which is why the grouping records them and rebuilds when they
        move. A setting that appears not to work is worse than one that
        is not there.
        """
        # NOT HOW PLACES ARE READ. That was here, and changing it rebuilt
        # every task from the tree at the next start: on a real calendar of
        # 2,843 states the rebuild merged twelve different pieces of work
        # into one and left most states in none. A new reading of places
        # applies from the next state on, as the appending rounds read
        # them - never by regrouping what is kept.
        return {"stands_out": self._stands_out(),
                "min_nodes": self._min_nodes(),
                "barely_taller": BARELY_TALLER,
                "enough_children": ENOUGH_CHILDREN}

    def _reshaped(self, was: Dict[str, Any]) -> List[str]:
        """The settings changed since the tasks were built - only those
        both record. One the record does not have was not changed: it did
        not exist (an upgrade adds settings; it must not regroup everyone),
        and one the record has that is no setting now is not one either."""
        shape = self._shape()
        return [name for name, value in shape.items()
                if was and name in was and was.get(name) != value]

    def _resummarise_after(self) -> float:
        return float(self.config.get("state_task_resummarise_after",
                                     RESUMMARISE_AFTER))

    def _accept_at(self) -> float:
        return float(self.config.get("state_task_accept_at", ACCEPT_AT))

    def _drift_limit(self) -> float:
        return float(self.config.get("state_task_drift_limit", DRIFT_LIMIT))

    def _rebuild_after(self) -> float:
        return float(self.config.get("state_task_rebuild_after", REBUILD_AFTER))

    def _loose_is_a_task(self) -> int:
        return max(2, int(self.config.get("state_task_loose_is_a_task",
                                          LOOSE_IS_A_TASK)))

    # -- tasks that outlive the round ---------------------------------------
    def _keep(self, nodes: Sequence[_Node],
              vectors: Dict[str, List[float]], source: Dict[str, Any],
              report: Dict[str, Any], kept: Kept, live: List[Task],
              scope: Dict[str, Any], where: _Where
              ) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
        """The tasks, carried forward from the last round where they can be.

        The old behaviour was to cluster from nothing every hour, which
        made a grouping that was right and an identity that was worthless:
        task 3 on Monday and task 3 on Tuesday were two unrelated things,
        so nothing could be said about a task over time and every summary
        was written again for a task that had gained one state.

        So the states that are new since last time are APPENDED to the
        task they are nearest, and the grouping is rebuilt only when a
        signal says appending has stopped being honest. Each signal's
        limit, and the measurement that chose it, is in `tasks.py`.

        What is drawn is still the whole tree - that is the shape the
        states make, and it is computed either way. What persists is which
        tasks there are.
        """
        # ONCE THERE ARE TASKS, THEY ARE NEVER REBUILT. New states are put
        # in the task they belong to or left loose; the loose ones become
        # tasks of their own; dormant tasks are picked up again. The full
        # regroup below is only for the first grouping, and for a calendar
        # read by words, where there is no centre to put a state near.
        # The one exception is deliberate: somebody changed how the grouping
        # is read. Those settings decide what a task is, so the grouping is
        # found again once with them - identities carried across as always -
        # or the setting would look broken.
        self._evidence_now = {}
        was = kept.shape_at_last_rebuild()
        reshaped = bool(self._reshaped(was))
        if (vectors and (live or Kept(self.db, where.scope).archived())
                and not (reshaped and not self._resuming_a_round())):
            return self._keep_continuous(nodes, vectors, source, report,
                                         kept, live, scope, where)
        known = {node.id for node in nodes}

        # A state this round no longer reads is forgotten by the task
        # that held it, and a task that held nothing else ends. This is
        # how the window moving forward reaches the tasks: what falls
        # behind the line is let go rather than held as an id nobody can
        # open.
        forgotten = 0
        for task in live:
            keeping = [one for one in task.members if one in known]
            if len(keeping) != len(task.members):
                forgotten += len(task.members) - len(keeping)
                task.members = keeping
                if vectors:
                    # A TASK'S CENTRE MOVES WHEN ITS STATES DO, and in a
                    # round that is only being finished they do not: the
                    # window is closed and the task set is frozen. The
                    # next round recomputes it from the states it reads.
                    self._refit(task, vectors)
        emptied = [task for task in live if not task.members]
        if emptied:
            kept.retire([task.id for task in emptied])
        live = [task for task in live if task.members]

        held = {one for task in live for one in task.members}
        # ALREADY OFFERED AND TAKEN BY NOTHING. Not arrivals: a state in
        # no task looks exactly like a state nobody has looked at, so
        # without remembering them each round offered the whole pile
        # again - re-processing what had been processed, and holding the
        # count of states that fit nowhere permanently above the limit at
        # which that means a task nobody has noticed. A first grouping
        # that left thirty-eight loose forced a rebuild of all thousand
        # on the next round, and on every round after it.
        was_waiting = kept.loose()
        waiting = [one for one in was_waiting
                   if one in known and one not in held]
        # And the pile lets go of what this round no longer reads, for
        # the same reason a task does: a row naming a state nobody can
        # open is a record of nothing. Filtering them out at every read
        # and leaving the rows behind would be a table that only grows.
        stale = [one for one in was_waiting if one not in known]
        if stale:
            kept.forget_loose(stale)
        settled = set(waiting) | held
        arrivals = [node.id for node in nodes if node.id not in settled]
        was = kept.states_at_last_rebuild()
        since = len(nodes) if was < 0 else max(0, len(nodes) - was)

        # Appending needs somewhere to put a state, which means vectors.
        # Read by words there are none, so every round is a rebuild - and
        # the identities still carry, because matching does not need them.
        if not live:
            why = ("Nothing was kept from a previous round, so this is the "
                   "first grouping.")
            appended, verdict = {"arrivals": len(arrivals), "ambiguous": 0,
                                 "placed": {}, "loose": list(arrivals)}, None
        elif not vectors:
            why = ("The states were read by words rather than by meaning, "
                   "and appending needs a place to put a state. The tasks "
                   "keep their identity through the rebuild all the same.")
            appended, verdict = {"arrivals": len(arrivals), "ambiguous": 0,
                                 "placed": {}, "loose": list(arrivals)}, None
        else:
            self._at(report, "appending the new states",
                     f"{len(arrivals)} of them, against {len(live)} task "
                     f"centres")
            appended = append_states(live, arrivals, vectors,
                                     accept_at=self._accept_at(),
                                     ambiguous_within=AMBIGUOUS_WITHIN)
            # What this round could not place joins what earlier rounds
            # could not place. The pile is what the loose signal counts.
            waiting = waiting + list(appended["loose"])
            verdict = should_rebuild(
                live, appended, waiting, len(nodes), since,
                drift_limit=self._drift_limit(),
                spread_limit=SPREAD_LIMIT,
                ambiguous_share=AMBIGUOUS_SHARE,
                rebuild_after=self._rebuild_after(),
                loose_is_a_task=self._loose_is_a_task())
            # A sixth question, asked here rather than in `should_rebuild`
            # because it is about the settings rather than about the
            # states: has the way the tree is read been changed since the
            # tasks were found?
            was = kept.shape_at_last_rebuild()
            moved = self._reshaped(was)
            verdict["signals"].append({
                "name": "the grouping is read differently now",
                "value": ", ".join(moved) if moved else "nothing changed",
                "limit": "any change",
                "fired": bool(moved),
                "why": ("These decide how the tree is read as tasks, so "
                        "changing one does nothing until it is read again. "
                        "Without this the setting would look broken.")})
            if moved:
                verdict["rebuild"] = True
                verdict["because"] = list(verdict["because"]) + [
                    f"the grouping is read differently now ({', '.join(moved)})"]
            why = ("; ".join(verdict["because"]) if verdict["rebuild"] else
                   "No signal fired, so the tasks are the ones already here.")

        # A pile of states that fit nowhere is a task nobody has noticed,
        # and finding it needs the PILE clustered - not the calendar. The
        # first version rebuilt everything here, which is a hundred times
        # the work to answer a question about a hundredth of the states,
        # and it threw away the appending it had just done.
        became: List[Task] = []
        pile_fired = bool(verdict) and any(
            signal["name"] == "the loose states are a task" and signal["fired"]
            for signal in (verdict or {}).get("signals", []))
        only_pile = pile_fired and not any(
            signal["fired"] for signal in (verdict or {}).get("signals", [])
            if signal["name"] != "the loose states are a task")
        # NOT WHILE A ROUND IS BEING FINISHED.
        #
        # A round reads a frozen window of states and names it over as
        # many visits as it takes, so the SET OF TASKS it is naming has
        # to be frozen too - otherwise the thing the reader is watching
        # fill in is not the thing that was started. Making new tasks out
        # of the leftovers on a later visit is how "4 of 7" became "6 of
        # 9" without a single name being lost: two tasks that did not
        # exist when the round began.
        #
        # The leftovers are not going anywhere. They are made into tasks
        # by the first visit of the NEXT round, which is where a change
        # of shape belongs - at a boundary the reader can see.
        if only_pile and vectors and not self._resuming_a_round():
            self._at(report, "looking at what fits nowhere",
                     f"{len(waiting)} states, clustered among themselves")
            self._live_for_pile = list(live)
            became, waiting = self._piles_apart(
                waiting, vectors, where, {node.id: node.leisure for node in nodes})
            if became:
                live.extend(became)
                verdict["rebuild"] = False
                verdict["because"] = []
                why = (f"{len(waiting)} state(s) still fit nowhere; "
                       f"{len(became)} new task(s) were made from the rest, "
                       f"without touching what was already here.")
            else:
                verdict["rebuild"] = False
                verdict["because"] = []
                why = (f"{len(waiting)} states fit nowhere, and nothing in "
                       f"them groups into a task of "
                       f"{self._min_nodes()} or more. They are kept as they "
                       f"are rather than rebuilt around.")
        rebuilding = verdict is None or verdict["rebuild"]
        # NOT WHILE A ROUND IS BEING FINISHED.
        #
        # A rebuild re-derives the tasks from the tree and matches them
        # against the rows already here - and where a match is not found
        # it RETIRES a row and creates another. The name, the weights and
        # the pieces live on the row, so the work keeps its identity only
        # as long as the row does: measured on the live folder, a round
        # that began at 16:52 retired three rows and created four at
        # 19:18, and the panel went from eight tasks named to five with
        # three of them saying "preparing" again. Nothing about the work
        # had changed. The round was only finishing.
        #
        # A visit that is finishing a round has its task set from the
        # visit that opened it. Deciding again is not a cheaper way of
        # reaching the same answer - it is a different answer, arrived at
        # from the same input, and it throws away everything the round
        # has written so far.
        if rebuilding and self._resuming_a_round():
            rebuilding = False
            why = ("This visit is finishing a round that already decided "
                   "what the tasks are. A rebuild would retire rows and "
                   "create others, and the names written so far belong to "
                   "the rows.")
            report["rebuild_deferred_to_next_round"] = True
        lineage: List[Dict[str, Any]] = []
        picture: Dict[str, Any] = {}
        if rebuilding and self._stop.is_set():
            # A rebuild is the long part of the round, and the join on
            # the way out cannot interrupt it once it has started. Asked
            # to stop on the very round that wanted one, the honest thing
            # is to keep what is here and rebuild when it next runs -
            # nothing is lost, because the signal that asked for it will
            # ask again.
            rebuilding = False
            why = ("A rebuild was due, but the application is closing. The "
                   "tasks are unchanged and it will run when it next does.")
            report["rebuild_deferred"] = True
        if rebuilding:
            # Only now is every pair scored and every state joined to
            # every other. This is the square of the calendar, and it is
            # the whole of what appending exists to avoid paying hourly.
            # ALREADY GROUPED IN THIS ROUND? Then it is grouped.
            #
            # A round reads a frozen window of states and then names them
            # a few at a time, over as many visits as it takes. Every one
            # of those visits used to score all the pairs again - measured
            # here on 789 states: 310,866 pairs, 3.6 seconds, and the same
            # answer every time, because the input is the same window by
            # construction. It is also the reason nothing could be shown
            # for the first six seconds of every visit.
            #
            # So the grouping belongs to the round, like the line it reads
            # up to and the list of what it has named. A new round throws
            # it away; an unfinished one picks it up.
            held = self._grouping_kept(nodes)
            if held is not None:
                merges = held["merges"]
                self._at(report, "picking up the grouping this round made",
                         f"{len(nodes)} states, scored once when the round "
                         f"began")
                shown, counted = held.get("pairs") or [], held.get("scored", 0)
                anchored = held.get("anchored", 0)
                reused = True
            else:
                self._at(report, "scoring every pair",
                         f"{len(nodes)} states, which is "
                         f"{len(nodes) * (len(nodes) - 1) // 2} pairs")
                distance, shown, counted, anchored = self._alike(nodes, vectors)
                self._at(report, "joining them into a tree",
                         f"{len(distance)} pairs close enough to matter")
                merges = cluster(len(nodes), distance)
                self._keep_grouping(nodes, merges, shown, counted, anchored)
                reused = False
            picture = tree(len(nodes), merges, [node.id for node in nodes])
            report["tree"] = picture
            report["similarity"] = {
                "source": source["how"],
                "model": source.get("model", ""),
                "read_ms": source.get("ms", 0),
                "unavailable": source.get("unavailable", ""),
                "pairs_scored": counted,
                "scored_now": not reused,
                "reused_from_this_round": reused,
                # What was decided before anything was scored: the states
                # that were open on the same file or page, put together
                # outright.
                "anchored": anchored,
                "pairs": shown,
            }
            self._at(report, "reading the tree as tasks",
                     "and matching them against the tasks already here")
            live, lineage = self._rebuild(nodes, merges, live, vectors,
                                          kept, where)
            # Read off the tree, a task may hold both kinds: the smaller
            # part, when it is a task's worth, made a task of its own.
            live.extend(self._kinds_apart(live, {node.id: node.leisure for node in nodes},
                                          kept, where, nodes, vectors))
        else:
            # Nothing was joined to anything. What this round scored is
            # each new state against each task's centre, which is a
            # handful of dot products - and the picture on the page is
            # the one the last rebuild drew, labelled as of then.
            report["tree"] = kept.last_tree()
            report["similarity"] = {
                "source": source["how"],
                "model": source.get("model", ""),
                "read_ms": source.get("ms", 0),
                "unavailable": source.get("unavailable", ""),
                "pairs_scored": len(arrivals) * len(live),
                "scored_now": False,
                "why": (f"{len(arrivals)} new state"
                        f"{'' if len(arrivals) == 1 else 's'} against "
                        f"{len(live)} task centre"
                        f"{'' if len(live) == 1 else 's'}. Every pair was "
                        f"not scored: that is the square of the calendar, "
                        f"and it is what this round exists not to pay."),
                "pairs": [],
            }
            when = task_now()
            for task in live:
                task.updated_at = when

        # BY SEAT, which a task is given once and keeps for life.
        #
        # This was `(created_at, -size)`, and that was almost right and
        # failed on the case that matters most: every task from the first
        # rebuild shares one `created_at` to the second, so the tie-break
        # decided the whole page, and the tie-break was size. Two tasks
        # that swapped sizes swapped places, and "the third row" was a
        # different piece of work each hour - which is the instability the
        # persistent ids were introduced to fix, coming back in the
        # reading order after being fixed in the data.
        #
        # A seat is a number handed out when a task first appears and
        # never reassigned. Anything new sits after everything that
        # already existed, so the page grows downwards and nothing above
        # moves.
        # What is in a task is no longer waiting; what is still waiting is
        # remembered, so the next round does not look at it again.
        if rebuilding:
            waiting = list(self._loose_after_rebuild.get(where.scope) or [])
            kept.forget_loose()
        placed_now = {one for task in live for one in task.members}
        kept.forget_loose([one for one in waiting if one in placed_now])
        waiting = [one for one in waiting if one not in placed_now]
        kept.set_loose(waiting)

        self._seat_them(live)
        live.sort(key=lambda task: (task.seat, task.created_at))
        for number, task in enumerate(live, start=1):
            task.number = number
        kept.save(live)
        # Where the next round reads from: everything up to here is in a
        # task or the pile.
        # The first grouping's line is where it began reading: what was
        # written before then was read or is before the beginning.
        if vectors and not self._resuming_a_round():
            self._processed_to(where, str(report.get("reads_until") or "")
                               or max((str(_moment(getattr(
                                   node, "updated_at", None)) or "")
                                   for node in nodes), default=""))

        new_states = set(appended.get("placed") or {})
        # WHAT EACH TASK'S MEMBERSHIP DID, by task. Two ways in - appended,
        # where the placement says which task took which state, and
        # rebuilt, where the task worked out what it had gained and what
        # had been taken away - and one answer, because the step that asks
        # "where does this stand now" does not care how a state moved.
        moved: Dict[str, Dict[str, List[str]]] = {}
        for state, task_id in (appended.get("placed") or {}).items():
            moved.setdefault(str(task_id), {"gained": [], "lost": []})
            moved[str(task_id)]["gained"].append(str(state))
        for task in live:
            happened = task.happened or {}
            got = list(happened.get("gained_ids") or [])
            went = list(happened.get("lost_ids") or [])
            if not got and not went:
                continue
            moved.setdefault(task.id, {"gained": [], "lost": []})
            moved[task.id]["gained"].extend(got)
            moved[task.id]["lost"].extend(went)
        report["moved"] = {
            key: {"gained": sorted(set(value["gained"])),
                  "lost": sorted(set(value["lost"]))}
            for key, value in moved.items()
            if value["gained"] or value["lost"]}
        # How tight each task is, measured from the vectors, and the same
        # answer all round. A visit that read no vectors has none of it -
        # and `_within` returns a LIST, one entry per task, so the empty
        # case has to be a list of empty measurements rather than nothing
        # at all. It was nothing at all for twenty minutes, and every
        # round died on `within[0]`.
        within = (self._within(live, vectors) if vectors
                  else [{} for _ in live])
        drawn = [self._drawn(task, nodes, within[index], index, new_states)
                 for index, task in enumerate(live)]
        # What no task would take. Named as that rather than hidden: these
        # are states that group with nothing, which is a finding about
        # them and not a gap in the picture.
        # Every state that fits nowhere, not just this round's - they are
        # one pile, and a reader wants to see the pile.
        report["loose"] = list(waiting)
        report["continuity"] = {
            "kept_tasks": len(live),
            "arrivals": len(arrivals),
            "placed": len(appended.get("placed") or {}),
            "loose": len(appended.get("loose") or []),
            "waiting": len(waiting),
            "became_tasks": [task.id for task in became],
            "ambiguous": int(appended.get("ambiguous") or 0),
            "forgotten": forgotten,
            "ended": [task.id for task in emptied],
            "since_rebuild": since,
            "window": dict(scope),
            # WHAT THIS ROUND WORKED ON, which on an appending round is
            # the handful that arrived and not the window it read to find
            # them. The difference is the whole of what appending is, so
            # a picture that shows the window is showing the wrong number.
            "processed": (sorted(node.id for node in nodes) if rebuilding
                          else sorted(arrivals)),
            "rebuilt": rebuilding,
            "why": why,
            "signals": (verdict or {}).get("signals", []),
            "lineage": lineage,
            "limits": {
                "accept_at": self._accept_at(),
                "drift_limit": self._drift_limit(),
                "spread_limit": SPREAD_LIMIT,
                "ambiguous_within": AMBIGUOUS_WITHIN,
                "ambiguous_share": AMBIGUOUS_SHARE,
                "rebuild_after": self._rebuild_after(),
                "loose_is_a_task": self._loose_is_a_task(),
                "same_task_at": SAME_TASK_AT,
                "resummarise_after": RESUMMARISE_AFTER,
            },
        }
        kept.record({"id": report.get("trace_id") or f"run-{task_now()}",
                     "ran_at": task_now(), "states": len(nodes),
                     "arrivals": len(arrivals),
                     "placed": len(appended.get("placed") or {}),
                     "loose": len(appended.get("loose") or []),
                     "ambiguous": int(appended.get("ambiguous") or 0),
                     "rebuilt": rebuilding,
                     "signals": (verdict or {}).get("signals", []),
                     "lineage": lineage,
                     "tree": picture,
                     "seen_from": scope.get("from", ""),
                     "shape": self._shape()})
        report["layers"] = drawn
        where = {node.id: index for index, node in enumerate(nodes)}
        groups = [[where[one] for one in task["states"] if one in where]
                  for task in drawn]
        return drawn, groups

    def _piles_apart(self, waiting: Sequence[str], vectors: Dict[str, List[float]],
                     where: _Where, leisure_of: Dict[str, Optional[bool]]
                     ) -> Tuple[List[Task], List[str]]:
        """What fits nowhere, made into tasks KIND BY KIND: the leisure among
        itself, the rest among itself - never one task of both."""
        enjoyed = [one for one in waiting if leisure_of.get(one) is True]
        rest = [one for one in waiting if leisure_of.get(one) is not True]
        made: List[Task] = []
        left: List[str] = []
        for group in (rest, enjoyed):
            if not group:
                continue
            these, still = self._tasks_from_pile(group, vectors, where)
            made += these
            left += still
        return made, left

    def _kinds_apart(self, live: List[Task], leisure_of: Dict[str, Optional[bool]],
                     kept: Kept, where: _Where, nodes: Sequence[_Node],
                     vectors: Dict[str, List[float]]) -> List[Task]:
        """A task holding both leisure and work, its smaller part taken out as
        a task of its own when it is a task's worth (`_split_off`)."""
        made: List[Task] = []
        for task in list(live):
            enjoyed = [one for one in task.members if leisure_of.get(one) is True]
            worked = [one for one in task.members if leisure_of.get(one) is False]
            if not enjoyed or not worked:
                continue
            smaller = enjoyed if len(enjoyed) <= len(worked) else worked
            if len(smaller) < self._min_nodes():
                continue
            new = self._split_off(
                task, smaller, kept, where,
                {"kind": "split from", "from": [task.id],
                 "why": "leisure and work are kept apart"},
                nodes=nodes, vectors=vectors)
            if new is not None:
                made.append(new)
        return made

    def _tasks_from_pile(self, waiting: Sequence[str],
                         vectors: Dict[str, List[float]],
                         where: _Where) -> Tuple[List[Task], List[str]]:
        """Group the states that fit nowhere, among themselves.

        The question "have the leftovers become a thing" is a question
        about the leftovers. Asking it by rebuilding the whole calendar is
        a hundred times the work for a hundredth of the states, and it
        throws away the appending already done - the tasks are renumbered,
        rematched and resummarised to answer something none of them were
        asked.

        So the pile is clustered by itself and the tree CUT WHERE IT IS
        TIGHT: the largest groups of five or more that are about as close
        together as the tasks already accepted. Reading it as a hierarchy
        was the first attempt and it lost the answer - when the pile did
        not divide, the whole of it came back as one candidate, failed for
        being about several things, and took the one real subject inside
        it down with it.

        Size is not the test and never was. Five states that each matched
        nothing are five strangers as easily as they are a new subject,
        and a grouping that calls any five of them a task invents work the
        user never did. The test is that a candidate is as tight as the
        loosest task this calendar has already accepted - a standard it
        set itself rather than one chosen here.
        """
        order = [one for one in waiting if one in vectors]
        least = self._min_nodes()
        if len(order) < 2 or self._work_in(order) <= self._least_work():
            return [], list(waiting)

        # HOW FAR OUT A REAL TASK'S EDGE IS, not where its middle sits.
        #
        # The mean alone was the first test and it let a tight core
        # swallow strangers: four identical states sit at nothing from
        # each other, which pulls the average down far enough to take in
        # two states about gardening and volcanoes. A task's own rule for
        # taking a state is its middle PLUS its spread, and a candidate
        # has to be judged by the same statistic or the two are not
        # comparable.
        # AGAINST A TYPICAL TASK, not the loosest. Measured against the
        # widest task there was, one broad task let through a "task" of a
        # hundred states whose middle was half again as far out as any named
        # one - and a task that wide takes in whatever fits nowhere else, for
        # good, now that nothing is regrouped.
        # JUDGED BY THE TASKS THE MODEL ACCEPTED. A group the pile made and
        # the model then turned down as no one thing is the loosest kind of
        # task there is, and measuring the next group against it let each
        # wide one excuse the next.
        judged = [task for task in self._live_for_pile
                  if task.size >= least
                  and (task.described or {}).get("concrete") is not False]
        edges = sorted(task.radius + 2.0 * task.spread for task in judged)
        allowed = edges[len(edges) // 2] if edges else 0.0
        allowed = (allowed * PILE_ALLOWANCE) if allowed else FARTHEST_PILE
        widest = max((task.radius for task in judged), default=0.0)
        middle_allowed = (widest * PILE_MIDDLE_ALLOWANCE if widest
                          else FARTHEST_PILE_MIDDLE)

        pairs = (self.embedder.pairwise(order, vectors)
                 if self.embedder is not None else [])
        distance = {(i, j): 1.0 - float(score) for i, j, score in pairs}
        merges = cluster(len(order), distance)
        children = {len(order) + index: (merge.left, merge.right)
                    for index, merge in enumerate(merges)}

        def members_of(node: int) -> List[int]:
            if node not in children:
                return [node]
            out: List[int] = []
            stack = [node]
            while stack:
                here = stack.pop()
                if here in children:
                    stack.extend(children[here])
                else:
                    out.append(here)
            return out

        def how_tight(seats: Sequence[int]) -> Tuple[float, float]:
            """Where this group's edge is, as a task measures its own.

            Middle plus twice the spread, which is the same shape as the
            rule a task uses to decide whether to take a state. A group
            of four identical states and two strangers has a small middle
            and an enormous spread, and it is the spread that says so.
            """
            rows = [vectors[order[seat]] for seat in seats]
            total = [sum(values) for values in zip(*rows)]
            middle = _unit_vector(total)
            apart = [1.0 - sum(a * b for a, b in zip(row, middle))
                     for row in rows]
            mean = sum(apart) / len(apart)
            spread = math.sqrt(sum((one - mean) ** 2 for one in apart)
                               / len(apart))
            return mean, mean + 2.0 * spread

        groups: List[List[str]] = []

        def cut(node: int) -> None:
            """Take the largest piece that is tight enough, or look inside."""
            seats = members_of(node)
            # NOT ENOUGH WORK, here or in anything inside it.
            if len(seats) < 2 or self._work_in(
                    [order[seat] for seat in seats]) <= self._least_work():
                return
            middle, edge = how_tight(seats)
            if edge <= allowed and middle <= middle_allowed:
                groups.append([order[seat] for seat in seats])
                return
            if node in children:
                for child in children[node]:
                    cut(child)

        for root in _roots(len(order), merges):
            cut(root)

        when = task_now()
        made: List[Task] = []
        for index, group in enumerate(groups):
            task = Task(id=f"task-{where.scope}-{_stamp(when)}"
                           f"-pile{index + 1}-{uuid.uuid4().hex[:6]}",
                        number=0, members=list(group), created_at=when,
                        born_of={"kind": "new", "from": [],
                                 "why": "it was what fitted nowhere else"})
            self._profile(task, self._places_now, self._when_now, vectors)
            task.happened = {"kind": "new", "from_the_pile": len(group),
                             "as_tight_as": round(allowed, 4),
                             "middle_within": round(middle_allowed, 4)}
            task.anchored()
            made.append(task)
        placed = {one for task in made for one in task.members}
        return made, [one for one in waiting if one not in placed]

    def _rebuild(self, nodes: Sequence[_Node], merges: Sequence[Any],
                 live: List[Task], vectors: Dict[str, List[float]],
                 kept: Kept, where: _Where
                 ) -> Tuple[List[Task], List[Dict[str, Any]]]:
        """Cluster from nothing, then line the result up against the old.

        The matching is the whole point of rebuilding this way. A task
        that is substantially the same thing keeps its id, its history and
        its summary; what split, merged, arrived or ended is recorded as
        that, rather than reaching the reader as a renumbering they would
        have to reverse-engineer from the sizes.
        """
        found = layers(len(nodes), merges, [node.id for node in nodes],
                       stands_out=self._stands_out(),
                       barely_taller=BARELY_TALLER,
                       worth_opening=self._min_nodes(),
                       enough_children=ENOUGH_CHILDREN,
                       deepest=self._deepest_layers())
        self._loose_after_rebuild[where.scope] = []
        if len(found) == 1 and found[0]["children"]:
            # A single root is the whole calendar, which is not a finding.
            self._loose_after_rebuild[where.scope] = list(
                found[0].get("loose") or [])
            found = list(found[0]["children"])
        groups = [list(part["states"]) for part in found]
        lined_up = match_tasks(live, groups, SAME_TASK_AT)

        when = task_now()
        out: List[Task] = []
        lineage: List[Dict[str, Any]] = []
        for index, row in enumerate(lined_up):
            members = list(row["members"])
            task = row["task"]
            # Before the branch below replaces it: whether this row is a
            # task that already existed. That is what decides whether it
            # keeps its place, and it cannot be read off the seat here
            # because seats are handed out after this returns.
            was_there = task is not None
            if task is not None:
                gained = sorted(set(members) - set(task.members))
                lost = sorted(set(task.members) - set(members))
                task.members = members
                task.version += 1
                task.happened = {"kind": row["kind"], "overlap": row["overlap"],
                                 "gained": len(gained), "lost": len(lost),
                                 # The ids, not only how many. `status` is
                                 # answered from the states that changed,
                                 # and a count cannot be read. Losses as
                                 # well as gains: a task that has had six
                                 # states taken out of it by a rebuild is
                                 # about less than it was, and the status
                                 # describing the larger thing is wrong.
                                 "gained_ids": gained, "lost_ids": lost}
            else:
                # The scope and six random characters, as well as the
                # moment. Without them two rebuilds in the same second
                # make the same id, and `save` updates by id - so a
                # sandbox round begun in the same second as a real one
                # overwrote the real task with made-up states and left it
                # marked live. The moment stays because a reader looking
                # at lineage wants to know when a task was born.
                task = Task(id=f"task-{where.scope}-{_stamp(when)}"
                               f"-{index + 1}-{uuid.uuid4().hex[:6]}",
                            number=index + 1,
                            members=members, created_at=when,
                            born_of={"kind": row["kind"],
                                     "from": list(row.get("from") or [])})
                task.happened = {"kind": row["kind"],
                                 "from": list(row.get("from") or [])}
            self._refit(task, vectors)
            task.anchored()
            task.rebuilt_at = when
            task.updated_at = when
            out.append(task)
            lineage.append({"task": task.id, "kind": row["kind"],
                            "overlap": row["overlap"],
                            "from": list(row.get("from") or []),
                            # Whether it kept its place or took a new one,
                            # which is the question a reader has when a row
                            # they knew is suddenly further down. Read from
                            # whether the task already existed, not from
                            # its seat: seats are handed out after this
                            # returns, so every new task would read -1 here
                            # and every old one would read whatever it had.
                            "kept_its_place": was_there,
                            "size": len(members)})

        carried = {task.id for task in out}
        ended = [task.id for task in live if task.id not in carried]
        if ended:
            kept.retire(ended, when)
            lineage.append({"ended": ended})
        # EVERY TASK LEAVES A FIRST GROUPING WITH A PROFILE, so the next
        # round reads only what is new and never regroups.
        if vectors:
            places_of, when_of = self._what_and_when(nodes)
            for task in out:
                self._profile(task, places_of, when_of, vectors)
                task.anchored()
        return out, lineage

    @staticmethod
    def _seat_them(live: List[Task]) -> None:
        """Give a seat to anything that has not got one.

        Two cases. A task that has just been created has no seat and takes
        the next free one, so it lands after everything already on the
        page - and that covers the extra pieces of a task that split,
        because one of those inherits the old task's identity and its seat
        along with it while the rest are new tasks that happen to have a
        parent.

        The other is the tasks that existed before seats did. They are
        seated in the order they are already being shown in, so the change
        moves nothing the first time it runs - a fix for an unstable order
        that reordered the page once on the way in would be a poor joke.
        """
        taken = [task.seat for task in live if task.seat >= 0]
        nobody = [task for task in live if task.seat < 0]
        if not nobody:
            return
        if not taken:
            # None of them has one: this is the first round after seats
            # were added, or the first round ever. Keep the order they
            # are in, which is the order they were already shown in.
            for seat, task in enumerate(nobody):
                task.seat = seat
            return
        free = max(taken) + 1
        # In the order they arrived in the rebuild, so a split's pieces
        # stay in the order the tree put them.
        for task in nobody:
            task.seat = free
            free += 1

    @staticmethod
    def _refit(task: Task, vectors: Dict[str, List[float]]) -> None:
        """Recompute a task's centre from the members it now holds."""
        rows = [vectors[one] for one in task.members if one in vectors]
        if not rows:
            task.total = []
            task.radius = task.spread = 0.0
            return
        task.total = [sum(values) for values in zip(*rows)]
        task.settle(vectors)

    # -- the continuous round ----------------------------------------------
    def _processed_until(self, where: _Where) -> str:
        """Up to when the states have been put in tasks."""
        if where.scope == SANDBOX:
            return str(self._marks.get(where.scope) or "")
        return str(self.config.get("state_processed_until", "") or "")

    def _processed_to(self, where: _Where, moment: str) -> None:
        if not moment:
            return
        if where.scope == SANDBOX:
            self._marks[where.scope] = moment
            return
        self.config.set("state_processed_until", moment, internal=True)

    def _what_and_when(self, nodes: Sequence[_Node]
                       ) -> Tuple[Dict[str, List[str]],
                                  Dict[str, Tuple[str, str]]]:
        """Each state's places - what it names and the folders and sites
        those sit in - and when it began and ended.

        Read again from the state's sources rather than taken from what was
        stored with it, so a better reading of the shapes the review writes
        reaches the states already written. And from its title and detail
        too, for the paths and addresses those spell out: a third of one
        project's states named its folder only there.
        """
        readings = {node.id: _workspace(
            list(node.sources),
            list(node.evidence) + [node.title, node.detail])
            for node in nodes}
        tools = _tools_in(readings.values())
        own = _canonical_place(str(self.config.get("agent_root") or ""))
        named_of: Dict[str, List[str]] = {}
        for node in nodes:
            keys = []
            for key in _identifying(readings[node.id], tools):
                # A window title's spinner or star is the program saying it
                # is busy, not part of the document's name: "✳ notes" and
                # "◑ notes" are one place.
                key = _canonical_place(_SPINNER_AHEAD.sub("", key).strip())
                if key and not _not_a_place(key, own):
                    keys.append(key)
            named_of[node.id] = keys
        # Folders this round has seen by their full path, by their last
        # part - so "personalized_agent_release" in a sentence, or a path
        # given relative to it, is that folder.
        folders: Dict[str, str] = {}
        for keys in named_of.values():
            for key in keys:
                if _is_a_path(key):
                    for one in [key] + _workspace_ancestors(key):
                        name = one.rsplit("/", 1)[-1]
                        if len(name) >= 8 and ("_" in name or "-" in name):
                            folders.setdefault(name, one)
        places_of: Dict[str, List[str]] = {}
        when_of: Dict[str, Tuple[str, str]] = {}
        for node in nodes:
            out: List[str] = []
            keys = list(named_of[node.id])
            words = set(_FOLDER_WORD.findall(
                _canonical_place(f"{node.title} {node.detail}")))
            keys += [folders[word] for word in words if word in folders]
            for key in keys:
                key = self._resolved(key, folders)
                for one in [key] + _workspace_ancestors(key):
                    if one and one not in out and not _not_a_place(one, own):
                        out.append(one)
            places_of[node.id] = out
        # The places that tell which work it was, and only those.
        pages_of: Dict[str, Set[str]] = {}
        for out in places_of.values():
            for place in out:
                site = _site_of(place)
                if site:
                    pages_of.setdefault(site, set()).add(place)
        for node in nodes:
            places_of[node.id] = _telling(places_of[node.id], pages_of)
            began = _utc(node.starts_at) or _utc(node.created_at) or ""
            # WHEN IT WAS LAST SEEN, not when it was expected to end. A
            # state's end is sometimes a guess at the future - "runs to
            # Friday" - and taken as activity it kept a task "active" for
            # weeks after anyone touched it. Capped at when it was written.
            written = _utc(getattr(node, "updated_at", None)) or began
            ended = _utc(node.ends_at) or began
            if written and ended and ended > written:
                ended = written
            when_of[node.id] = (began, ended)
        return places_of, when_of

    def _resolved(self, key: str, folders: Dict[str, str]) -> str:
        """A path written relative to a project, as the project's path.

        `personalized_agent/heartbeat/states/tasks.py` and the folder
        `c:/users/me/desktop/personalized_agent_release` are one project,
        and nothing in the two strings says so. The disk does: if the file
        is there under a folder this round has seen, that is where it is.
        """
        if not key or _is_a_path(key) or "/" not in key or "." in key.split(
                "/", 1)[0]:
            return key
        held = self._resolved_paths.get(key)
        if held is not None:
            return held or key
        found = ""
        for root in sorted(set(folders.values()), key=len, reverse=True):
            candidate = f"{root}/{key}"
            try:
                if os.path.exists(candidate):
                    found = _canonical_place(candidate)
                    break
            except OSError:
                continue
        self._resolved_paths[key] = found
        return found or key

    @staticmethod
    def _profile(task: Task, places_of: Dict[str, List[str]],
                 when_of: Dict[str, Tuple[str, str]],
                 vectors: Dict[str, List[float]]) -> None:
        """Give a task its profile from the states of it that are here,
        taken in the order they happened - the same as if it had been built
        one state at a time."""
        members = [one for one in task.members if one in vectors]
        members.sort(key=lambda one: when_of.get(one, ("", ""))[0])
        task.start_profile()
        task.members = []
        for one in members:
            began, ended = when_of.get(one, ("", ""))
            task.take(one, vectors[one], places_of.get(one, ()), began, ended)
        # THE SPREAD FROM THE FINISHED CENTRE. Taken one at a time, each
        # early state is measured against a centre that is still forming,
        # and the first few distances are large - a task built that way
        # comes out half again as wide as it is, and takes in what it
        # should not. So the spread is measured against the centre it ends
        # with, each state weighted as the centre weighs it; states added
        # after this are measured as they arrive, which is all they can be.
        if members and task.total:
            keep = 0.5 ** (1.0 / max(1, HALF_LIFE_STATES))
            weights = [keep ** (len(members) - 1 - index)
                       for index in range(len(members))]
            centre = task.centre
            apart = [1.0 - _cosine_of(vectors[one], centre) for one in members]
            total = sum(weights) or 1.0
            mean = sum(w * d for w, d in zip(weights, apart)) / total
            spread = math.sqrt(sum(w * (d - mean) ** 2
                                   for w, d in zip(weights, apart)) / total)
            task.radius, task.spread = mean, spread

    #: A task smaller than this is not re-checked: one state out of five
    #: is too little to judge the rest by.
    RECHECK_FROM = 6
    #: The most of the record of what was taken out kept, newest last.
    TAKEN_OUT_KEPT = 1000

    def _take_out_other_work(self, task: Task, state_ids: Sequence[str], where: _Where,
                             report: Dict[str, Any], by: str) -> List[str]:
        """NOT THIS WORK: states the model, summing a task up or saying where
        it stands, said are plainly about something else - taken out to the
        pile - which is offered to no task again, only made a task of its
        own where enough of it is alike, so nothing bounces back. Made in the
        call already made for the task: no call of its own.

        A task smaller than RECHECK_FROM keeps them; and one that would lose
        more than half of itself keeps them - then they may be what it is.
        What was taken out is kept, newest last, to be read and undone."""
        members = list(task.members)
        out = [one for one in dict.fromkeys(state_ids) if one in members]
        if not out or len(members) < self.RECHECK_FROM or len(out) * 2 > len(members):
            return []
        # OFF until measured against the user's labelled week: what the
        # model said is only noted on the round's report, for a reader to
        # look at, and nothing is moved.
        if not bool(self.config.get("state_take_out_other_work", False)):
            said = {"at": task_now(), "task": task.id, "states": list(out), "by": by}
            report.setdefault("not_this_work_said", []).append(said)
            # Kept, newest last, for the page to show (State heartbeat
            # debugger): what would have been taken out, and nothing was.
            if where.scope == LIVE:
                kept_said = list(self.config.get("state_not_this_work_said") or [])
                kept_said.append(said)
                self.config.set("state_not_this_work_said", kept_said[-300:], internal=True)
            return []
        try:
            nodes = [_Node(state) for state in where.states.by_ids(members)]
            vectors, _how = self._vectors(nodes, where)
        except Exception:
            log.debug("could not take states out of %s", task.id, exc_info=True)
            return []
        places_of, when_of = self._what_and_when(nodes)
        gone = set(out)
        rest = [one for one in members if one not in gone]
        if not isinstance(task.profile.get("summarised"), list):
            task.profile["summarised"] = list(members)
        said_apart = list(task.profile.get("not_the_same") or [])
        task.members = rest
        if vectors:
            self._profile(task, places_of, when_of, vectors)
            # Its states that could not be read stay in it, unjudged.
            task.members = task.members + [one for one in rest
                                           if one not in vectors and one not in task.members]
        task.profile["not_the_same"] = said_apart
        # Offered back to this task only in a round it gains others too.
        kicked = [one for one in task.profile.get("kicked_out") or [] if one not in gone]
        task.profile["kicked_out"] = (kicked + out)[-500:]
        task.happened = {"kind": "not this work", "lost_ids": list(out), "by": by}
        kept = Kept(self.db, where.scope)
        kept.save([task])
        kept.set_loose(out)
        entry = {"at": task_now(), "task": task.id, "states": list(out), "by": by}
        report.setdefault("not_this_work", []).append(entry)
        if where.scope == LIVE:
            record = list(self.config.get("state_not_this_work") or [])
            record.append(entry)
            self.config.set("state_not_this_work", record[-self.TAKEN_OUT_KEPT:], internal=True)
        return out

    def _recheck_members_once(self, live: List[Task], kept: Kept, where: _Where,
                              report: Dict[str, Any]) -> None:
        """EVERY TASK'S STATES JUDGED AGAIN, ONCE, by the rules now.

        States went into tasks by rules since made stricter - reading a
        WeChat article or the new-tab news while a robot task was the
        nearest kept a robot task "active" for two weeks. A new rule only
        judges new states, so each state already in a task is judged as a
        new one would be, against the task WITHOUT it: one that would not be
        placed there now goes to the pile, where the next rounds find it a
        task. Nothing is merged and nothing regrouped. A task that would
        lose more than half of itself is left as it is - then the states
        that do not fit may be what it is. What moved is kept, to be read
        and undone."""
        if self.config.get("state_members_rechecked"):
            return
        moved: Dict[str, List[str]] = {}
        left_alone: List[str] = []
        specific = _specificity(live)
        for task in live:
            if len(task.members) < self.RECHECK_FROM:
                continue
            try:
                nodes = [_Node(state) for state in where.states.by_ids(list(task.members))]
                vectors, _how = self._vectors(nodes, where)
            except Exception:
                log.debug("could not re-check %s", task.id, exc_info=True)
                continue
            if not vectors:
                continue
            places_of, when_of = self._what_and_when(nodes)
            judged = [one for one in task.members if one in vectors]
            misfits = []
            for one in judged:
                probe = copy.deepcopy(task)
                probe.members = [other for other in task.members if other != one]
                self._profile(probe, places_of, when_of, vectors)
                said = _fits(probe, vectors[one], places_of.get(one, ()), specific,
                             self._accept_at())
                if not said["fits"]:
                    misfits.append(one)
            if not misfits:
                continue
            if len(misfits) * 2 > len(judged):
                left_alone.append(task.id)
                continue
            out = set(misfits)
            rest = [one for one in task.members if one not in out]
            unread = [one for one in rest if one not in vectors]
            if not isinstance(task.profile.get("summarised"), list):
                task.profile["summarised"] = list(task.members)
            said_apart = list(task.profile.get("not_the_same") or [])
            task.members = rest
            self._profile(task, places_of, when_of, vectors)
            # Its states the round could not read stay in it, unjudged.
            task.members = task.members + [one for one in unread if one not in task.members]
            task.profile["not_the_same"] = said_apart
            task.happened = {"kind": "rechecked", "lost_ids": list(misfits)}
            kept.save([task])
            kept.set_loose(misfits)
            moved[task.id] = list(misfits)
        record = {"at": task_now(), "moved": moved, "left_alone": left_alone}
        self.config.set("state_members_rechecked", record, internal=True)
        report["rechecked"] = record
        log.warning("re-checked the tasks' states: %d moved out of %d tasks; %d left alone",
                    sum(len(one) for one in moved.values()), len(moved), len(left_alone))

    def _split_off(self, task: Task, taken: Sequence[str], kept: Kept,
                   where: _Where, born_of: Dict[str, Any],
                   described: Optional[Dict[str, Any]] = None,
                   nodes: Optional[Sequence[_Node]] = None,
                   vectors: Optional[Dict[str, List[float]]] = None
                   ) -> Optional[Task]:
        """Take these states out of a task and make them a task of their own.

        Both are given their profiles again from their states, the same way
        a task is first given one - the old centre was the two together.
        The task they came from keeps its name until a round asks again,
        which its centre having moved makes happen.
        """
        taken_set = set(taken)
        rest = [one for one in task.members if one not in taken_set]
        if not rest or self._work_in(list(taken_set)) <= self._least_work():
            return None
        # What it was before losing them, if nothing better says what it was
        # named from: losing a third of itself is a change of what it is.
        if not isinstance(task.profile.get("summarised"), list):
            task.profile["summarised"] = list(task.members)
        if nodes is None:
            nodes = [_Node(state)
                     for state in where.states.by_ids(list(task.members))]
        if vectors is None:
            vectors, _how = self._vectors(nodes, where)
        if not vectors:
            return None
        places_of, when_of = self._what_and_when(nodes)
        when = task_now()
        made = Task(id=f"task-{where.scope}-{_stamp(when)}"
                       f"-split-{uuid.uuid4().hex[:6]}",
                    number=0, members=[one for one in task.members
                                       if one in taken_set],
                    created_at=when, born_of=dict(born_of))
        self._profile(made, places_of, when_of, vectors)
        if not made.members:
            return None
        made.anchored()
        if described:
            made.described = dict(described)
            made.named()
            made.summary_at = when
            made.summary_members = made.size
        made.happened = {"kind": "split from", "from": [task.id],
                         "states": len(made.members)}
        said_apart = list(task.profile.get("not_the_same") or [])
        task.members = rest
        self._profile(task, places_of, when_of, vectors)
        task.profile["not_the_same"] = said_apart
        # OTHER WORK STAYS APART. The closeness check asks whether two
        # nearby tasks are one, and asked about a pair the model had just
        # separated - and got the other answer. Said once is said.
        if born_of.get("other_work"):
            task.profile["not_the_same"] = said_apart + [made.id]
            made.profile["not_the_same"] = [task.id]
        task.happened = {"kind": "lost", "to": made.id,
                         "lost_ids": list(made.members)}
        made.seat = task.seat
        kept.save([task, made])
        return made

    def _named_from_summaries(self, live: Sequence[Task], where: _Where) -> None:
        """Which states each task's name was written from, for the tasks
        named before that was kept - read back from the summary record,
        whose references name the states it was written from.

        Without it, a task that gained a day of other work and then lost it
        to a split was the same size as when it was named, and kept a name
        for work it no longer held."""
        library = getattr(where, "library", None)
        for task in live:
            if not task.summary_id or isinstance(
                    task.profile.get("summarised"), list):
                continue
            record = None
            for folder in ("state_tasks", None):
                try:
                    record = library.get(task.summary_id, folder=folder) \
                        if library is not None else None
                except Exception:        # pragma: no cover - a read
                    record = None
                if record is not None:
                    break
            read = [str(one)[len("state: "):] for one in
                    (getattr(record, "references", None) or [])
                    if str(one).startswith("state: ")]
            if read:
                task.profile["summarised"] = read
                task.profile["summarised_sampled"] = True

    def _take_out_other_places(self, live: Sequence[Task],
                               places_of: Dict[str, List[str]],
                               vectors: Dict[str, List[float]],
                               specific: Dict[str, float], kept: Kept,
                               where: _Where, report: Dict[str, Any]
                               ) -> List[Task]:
        """Each task's states that work somewhere the rest of it never does,
        taken out as a task of their own (`tasks.apart_by_place`).

        A day on a new project went into an email task because the mail was
        open beside it, and once the first few were in, the task held the
        project's folder and the rest followed - so the project never
        appeared, under a name about emails. Found by the place alone it
        would not be enough: the group must also read as other work, and
        hold more work than a task holds. Taken out as other work, so the
        closeness check does not put it straight back.
        """
        made: List[Task] = []
        for task in list(live):
            if task.retired_at or task.archived_at:
                continue
            found = apart_by_place(task, places_of, vectors, specific,
                                   self._min_nodes())
            if not found:
                continue
            new = self._split_off(
                task, found["states"], kept, where,
                {"kind": "split from", "from": [task.id], "other_work": True,
                 "why": f"its work is in {found['place']}, where the rest "
                        f"of the task never was"})
            report.setdefault("taken_out_by_place", []).append(
                {"from": task.id, "place": found["place"],
                 "states": len(found["states"]), "apart": found["apart"],
                 "wide": found["wide"], "made": new.id if new else ""})
            if new is not None:
                made.append(new)
        return made

    def _take_out_what_is_buried(self, task: Task, buried: Dict[str, Any],
                                 kept: Kept, where: _Where,
                                 report: Dict[str, Any]) -> Optional[Task]:
        """The model turned a group down and named a task inside it.

        Hiding the group hid the task with it. So the task is taken out:
        the shown states the model said were about it, and every other
        state of the group that is closer to one of those than to any
        other shown state - each sample stands for the states like it.
        """
        seeds = set(buried.get("states") or [])
        nodes = [_Node(state)
                 for state in where.states.by_ids(list(task.members))]
        vectors, _how = self._vectors(nodes, where)
        shown = [one for one in buried.get("shown") or [] if one in vectors]
        if not vectors or not any(one in seeds for one in shown) \
                or all(one in seeds for one in shown):
            return None
        taken = []
        for member in task.members:
            vector = vectors.get(member)
            if vector is None:
                continue
            nearest = max(shown, key=lambda one: _cosine_of(vector,
                                                            vectors[one]))
            if nearest in seeds:
                taken.append(member)
        title = str(buried.get("title") or "")
        made = self._split_off(
            task, taken, kept, where,
            {"kind": "split from", "from": [task.id], "other_work": True,
             "why": "the model found it inside a group it judged to be no "
                    "one task" + (f": {title}" if title else "")},
            nodes=nodes, vectors=vectors)
        report.setdefault("taken_out", []).append(
            {"from": task.id, "title": title, "states": len(taken),
             "made": made.id if made is not None else ""})
        return made

    def _undo_old_merges(self, live: List[Task], kept: Kept,
                         report: Dict[str, Any], where: _Where) -> List[Task]:
        """Ask once of every task made by merging: was each part the same work?

        Merging was done by closeness alone before tasks were kept from
        round to round, and closeness put a paper's revision into the
        agent's own rework because a few of its states were about the
        agent's notes on it. The parts are still known - each was a task
        with a name - so each is held against what the merge became, and
        one the model says is other work is taken back out, with its name.
        """
        made: List[Task] = []
        # WHAT WAS JOINED IN LATER, as well as what a merge was made of: a
        # task taken out as other work was joined straight back by the next
        # round's closeness check, and a five-state reading task went into a
        # garden-watering set-up because three of its states were in both.
        joined_into: Dict[str, List[Task]] = {}
        for old in kept.all(500):
            born = old.born_of or {}
            if old.retired_at and born.get("joined_into") \
                    and not born.get("rechecked"):
                joined_into.setdefault(str(born["joined_into"]), []).append(old)

        def other_work(one: Task) -> bool:
            born = one.born_of or {}
            return bool(born.get("other_work") or born.get("was"))

        for task in list(live):
            born = dict(task.born_of or {})
            merged = (born.get("kind") == "merged from"
                      and not born.get("rechecked"))
            parts: List[Tuple[Task, str]] = []
            if merged:
                named = list(born.get("from") or [])
                found = {one.id: one for one in kept.by_ids(named)}
                parts += [(found[one], "ask") for one in named if one in found]
            for old in joined_into.get(task.id, []):
                why = str((old.born_of or {}).get("why_joined") or "")
                if other_work(old):
                    # The model already said it was other work; a join that
                    # undid that was the closeness check not knowing.
                    parts.append((old, "other work"))
                elif why == "the same states, found twice":
                    parts.append((old, "ask"))
            if not parts:
                continue
            if not self._going(where):
                break
            members = set(task.members)
            asked_about = []
            for parent, how in parts:
                inside = [one for one in task.members
                          if one in set(parent.members)]
                said = dict(parent.described or {})
                # A part joined in later is looked at once; a merge's parts
                # are marked done on the merge itself, below.
                if parent.id not in set(born.get("from") or []):
                    parent.born_of = dict(parent.born_of or {},
                                          rechecked=task_now())
                    kept.save([parent])
                turned_down = said.get("concrete") is False and how == "ask"
                if not said.get("title") or turned_down \
                        or self._work_in(inside) <= self._least_work() \
                        or not (members - set(inside)):
                    continue
                if how == "ask":
                    self._at(report, "asking whether a merged task was one task",
                             f"{_a_real_name(said)[:60]}")
                    asked = "\n".join(["TASK A"] + self._task_said(parent)
                                      + [""] + ["TASK B"]
                                      + self._task_said(task))
                    answer = self._ask_whether(SAME_TASK_PROMPT, asked, report,
                                               "merged part")
                    asked_about.append({"part": parent.id, **answer})
                    if answer["same"]:
                        continue
                    why = f"taken back out of a merge: {answer['why']}"
                else:
                    asked_about.append({"part": parent.id, "same": False,
                                        "why": "split out as other work "
                                               "before, and joined back "
                                               "without being asked"})
                    why = "taken back out: it had been split out as other work"
                back = self._split_off(
                    task, inside, kept, where,
                    {"kind": "split from", "from": [task.id],
                     "was": parent.id, "other_work": True, "why": why},
                    described=said)
                if back is not None:
                    made.append(back)
                    live.append(back)
                    members = set(task.members)
            if merged:
                task.born_of = dict(born, rechecked=task_now())
            kept.save([task])
            report.setdefault("merges_rechecked", []).append(
                {"task": task.id, "asked": asked_about})
        return made

    def _ask_whether(self, prompt: str, asked: str, report: Dict[str, Any],
                     what: str) -> Dict[str, Any]:
        """One yes-or-no question about the grouping, put to the model."""
        out: Dict[str, Any] = {"asked": asked, "same": False, "why": ""}
        if self.models is None:
            out["why"] = "no model to ask"
            return out
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": asked}],
                             system=prompt, max_tokens=300,
                             task="state_heartbeat",
                             # A decision about the grouping, the agent's own.
                             language=in_english("the answer and why"),
                             metadata={"kind": what}),
                role="heartbeat")
        except ModelUnavailable as exc:
            out["why"] = f"could not ask: {exc}"[:300]
            return out
        except Exception as exc:              # pragma: no cover - a question
            out["why"] = f"could not ask: {exc}"[:300]
            return out
        reply = getattr(response, "text", "") or ""
        answered = _as_json(reply) or {}
        self._spent(report, {"calls": 1,
                             "tokens_in": int(getattr(getattr(
                                 response, "usage", None), "input_tokens", 0)
                                 or 0),
                             "tokens_out": int(getattr(getattr(
                                 response, "usage", None), "output_tokens", 0)
                                 or 0)})
        out["answer"] = reply
        out["same"] = answered.get("same") is True
        out["why"] = str(answered.get("why") or "")[:500]
        return out

    @staticmethod
    def _task_said(task: Task) -> List[str]:
        held = dict(task.described or {})
        lines = [f"title: {_a_real_name(held) or '(not named)'}",
                 f"summary: {held.get('summary') or '(none)'}",
                 f"status: {held.get('status') or '(none)'}",
                 f"worked on from {task.first_seen[:10] or '?'} to "
                 f"{task.last_seen[:10] or '?'}, {task.count} states",
                 f"mostly in: {', '.join(task.region()) or '(no file or site)'}"]
        return lines

    #: How many dormant tasks one round may ask about, and how many pairs
    #: of tasks it may ask whether to join - each is a model call.
    ASKS_PER_ROUND = 3

    def _keep_continuous(self, nodes: Sequence[_Node],
                         vectors: Dict[str, List[float]],
                         source: Dict[str, Any], report: Dict[str, Any],
                         kept: Kept, live: List[Task],
                         scope: Dict[str, Any], where: _Where
                         ) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
        """The tasks, carried on: new states placed, nothing regrouped.

        A task keeps a profile - a centre that leans towards its recent
        work, its spread, its places - and so a new state is judged against
        the task rather than against every state the task ever held. It
        joins the task it fits, by meaning and by workspace; what fits none
        waits in the pile, and the pile becomes new tasks. A task nobody has
        worked on for a month goes dormant: it is not drawn, and a state
        that belongs to it later picks it up again, history and name intact.
        """
        known = {node.id: node for node in nodes}
        places_of, when_of = self._what_and_when(nodes)
        self._places_now, self._when_now = places_of, when_of
        resuming = self._resuming_a_round()
        stamp = task_now()
        # The re-check of the states already in tasks (_recheck_members_once)
        # is NOT run: previewed read-only on the user's tasks it moved real
        # work out with the misfits and left the task it was for alone. To
        # be measured against the labelled week before it runs anywhere.

        # A TASK FROM BEFORE PROFILES is given one from the states of it
        # this round holds - which on the round that first does this is the
        # newest thousand, as it always was.
        migrated = []
        for task in live:
            if not task.profiled:
                self._profile(task, places_of, when_of, vectors)
                task.anchored()
                migrated.append(task.id)

        # THE SAME STATES, FOUND TWICE, ARE ONE TASK. A pile that was not
        # emptied made a copy of the same task every round; tasks that hold
        # mostly the same states are joined into the older one, which keeps
        # its name, and the copy is retired saying so.
        copies = []
        for index, one in enumerate(live):
            if one.retired_at:
                continue
            for other in live[index + 1:]:
                mine, theirs = set(one.members), set(other.members)
                if other.retired_at or not mine or not theirs:
                    continue
                # NEARLY ALL OF THE SMALLER, AND ABOUT THE SAME THING. Half
                # was the line, and a five-state reading task with three
                # states left over in a watering set-up passed it.
                if len(mine & theirs) < 0.8 * min(len(mine), len(theirs)):
                    continue
                if one.total and other.total and (
                        1.0 - _cosine_of(one.centre, other.centre)
                        > max(one.radius, other.radius)):
                    continue
                older, newer = sorted((one, other), key=lambda task: (
                    task.created_at, task.id))
                older.absorb(newer)
                newer.retired_at = stamp
                newer.born_of = dict(newer.born_of, joined_into=older.id,
                                     why_joined="the same states, found twice")
                copies.append(newer.id)
                if newer is one:
                    break
        if copies:
            kept.save([task for task in live if task.retired_at])
            live[:] = [task for task in live if not task.retired_at]
        report["copies_joined"] = copies

        # DORMANT AFTER A MONTH, not ended. A task with nothing of it left
        # to read is dormant too: it is kept, and can be picked up again.
        went_quiet = []
        archive = kept.archived()
        for task in list(live):
            if not task.total or task.phase() == "archived":
                task.archived_at = stamp
                task.happened = {"kind": "dormant"}
                live.remove(task)
                archive.append(task)
                went_quiet.append(task.id)

        settled = ({one for task in live + archive for one in task.members}
                   | set(kept.loose()))
        fresh = self._fresh_ids.get(where.scope)
        arrivals = [node.id for node in nodes
                    if node.id not in settled
                    and (fresh is None or node.id in fresh)]
        arrivals.sort(key=lambda one: when_of.get(one, ("", ""))[0])
        specific = _specificity(live + archive)
        self._specific_now = specific

        # A ROUND BEING FINISHED places nothing: its tasks were decided
        # when it began, and the states that arrived since are the next
        # round's.
        if resuming:
            arrivals = []
        self._at(report, "putting the new states in their tasks",
                 f"{len(arrivals)} new, against {len(live)} task(s), by "
                 f"meaning and by where the work is")
        leisure_of = {node.id: node.leisure for node in nodes}
        self._leisure_now = leisure_of
        # THE PILE OFFERED AGAIN, every round, with the new states: a task
        # that has grown may now be where one of them belongs. Offered is
        # not taken - each goes in only where it fits as a new state would,
        # and then the task's next status call may take it out again.
        offered = list(arrivals)
        if (not resuming and bool(self.config.get("state_task_reoffer_pile", False))):
            new = set(arrivals)
            offered += [one for one in kept.loose() if one in known and one not in new]
            offered.sort(key=lambda one: when_of.get(one, ("", ""))[0])
        # A STATE THE MODEL TOOK OUT OF A TASK is offered back to that task
        # only in a round the task gains others too - then it rides on the
        # status call made for them, and never brings a call of its own.
        barred: Dict[str, set] = {}
        for task in live:
            for one in task.profile.get("kicked_out") or []:
                barred.setdefault(str(one), set()).add(task.id)
        placed = place_states(live, offered, vectors, places_of, when_of,
                              specific, accept_at=self._accept_at(),
                              dormant=archive, leisure_of=leisure_of, barred=barred)
        gained = set((placed.get("placed") or {}).values())
        again = [one for one in placed["loose"] if barred.get(one, set()) & gained]
        if again:
            second = place_states([task for task in live if task.id in gained], again,
                                  vectors, places_of, when_of, specific,
                                  accept_at=self._accept_at(), leisure_of=leisure_of)
            for one, task_id in (second.get("placed") or {}).items():
                placed["placed"][one] = task_id
                task = next((each for each in live if each.id == task_id), None)
                if task is not None:
                    task.profile["kicked_out"] = [x for x in task.profile.get("kicked_out") or []
                                                  if x != one]
            taken_back = set(second.get("placed") or {})
            placed["loose"] = [one for one in placed["loose"] if one not in taken_back]
            placed.setdefault("evidence", {}).update(second.get("evidence") or {})
        report["pile_offered"] = len(offered) - len(arrivals)
        self._evidence_now = dict(placed.get("evidence") or {})
        loose = list(placed["loose"])
        revived: List[Dict[str, Any]] = []
        # Picked up where it was left: work in a dormant task's own places.
        for task_id, states in (placed.get("woken") or {}).items():
            task = next((one for one in archive if one.id == task_id), None)
            for state in states:
                if task is not None:
                    place_said = (placed["evidence"].get(state) or {}).get(
                        "place", "")
                    self._wake(task, live, archive, revived, state, vectors,
                               places_of, when_of, f"it shares the task's "
                               f"place {place_said}", stamp)

        # PICKED UP AGAIN, on the wording alone. What fits no task now may
        # still belong to one that has been dormant without sharing its
        # places - that is asked rather than assumed.
        to_ask: Dict[str, List[str]] = {}
        for state in list(loose):
            vector = vectors.get(state)
            if not vector or not archive:
                continue
            best = None
            mine = leisure_of.get(state)
            for task in archive:
                theirs = _leaning(task, leisure_of)
                if mine is not None and theirs is not None and theirs != mine:
                    continue
                said = _fits(task, vector, places_of.get(state, ()), specific,
                             self._accept_at())
                if said["fits"] and (best is None or said["rank"] > best[0]):
                    best = (said["rank"], task, said)
            if best is None:
                continue
            _rank, task, said = best
            if said["workspace"] >= REVIVE_AT:
                self._wake(task, live, archive, revived, state, vectors,
                           places_of, when_of, f"it shares the task's place "
                           f"{said['place']}", stamp)
                loose.remove(state)
            else:
                to_ask.setdefault(task.id, []).append(state)
        for task_id, states in list(to_ask.items())[:self.ASKS_PER_ROUND]:
            task = next((one for one in archive if one.id == task_id), None)
            if task is None:
                continue
            asked = "\n".join(
                ["THE DORMANT TASK"] + self._task_said(task)
                + ["", f"{len(states)} NEW STATE(S):", ""]
                + [line for number, one in enumerate(states[:6], start=1)
                   if one in known
                   for line in _whole_state(known[one].to_dict(),
                                            f"new {number}")])
            answer = self._ask_whether(SAME_WORK_AGAIN_PROMPT, asked, report,
                                       "same work again")
            report.setdefault("asked_about_dormant", []).append(
                {"task": task.id, "states": list(states), **answer})
            if answer["same"]:
                for state in states:
                    self._wake(task, live, archive, revived, state, vectors,
                               places_of, when_of,
                               f"asked: {answer['why']}", stamp)
                    loose.remove(state)

        # WHAT EACH WAS NAMED FROM, for a task named before that was kept:
        # read back once from its summary, which names the states it read.
        if not resuming:
            self._named_from_summaries(live, where)

        # WORK LET INTO A TASK IT IS NOT, given back. A group of a task's
        # states that all work in one telling place the rest of it never
        # touches, and that read as other work, is taken out as its own task.
        if not resuming and not self._stop.is_set():
            live.extend(self._take_out_other_places(live, places_of, vectors,
                                                    specific, kept, where,
                                                    report))

        # THE PILE: what has fitted nothing, from this round and before.
        # Let go of after a month, when it was not the start of anything.
        since = kept.loose_since()
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=LOOSE_KEPT_DAYS)).isoformat()
        stale = [one for one, when in since.items() if when and when < cutoff]
        if stale:
            kept.forget_loose(stale)
        waiting = [one for one in kept.loose()
                   if one not in set(stale) and one in known]
        waiting = list(dict.fromkeys(waiting + loose))
        became: List[Task] = []
        joined_to_dormant: List[str] = []
        if (len(waiting) >= self._min_nodes() and not resuming
                and not self._stop.is_set()):
            self._at(report, "looking at what fits nowhere",
                     f"{len(waiting)} states, clustered among themselves")
            self._live_for_pile = list(live) + list(archive)
            made, waiting = self._piles_apart(waiting, vectors, where, leisure_of)
            for task in made:
                # A NEW TASK MAY BE AN OLD ONE, resumed somewhere new. Five
                # states together say more than one, so the pile's tasks
                # are looked at against the dormant ones too.
                match = None
                for old in archive:
                    said = _fits(old, task.centre, list(task.places()),
                                 specific, self._accept_at())
                    if said["fits"] and (match is None
                                         or said["rank"] > match[0]):
                        match = (said["rank"], old, said)
                if match is not None:
                    _rank, old, said = match
                    same = said["workspace"] >= REVIVE_AT
                    why = f"it shares the task's place {said['place']}"
                    if not same:
                        asked = "\n".join(
                            ["THE DORMANT TASK"] + self._task_said(old)
                            + ["", f"{task.count} NEW STATE(S):", ""]
                            + [line for number, one in enumerate(
                                task.members[:6], start=1) if one in known
                               for line in _whole_state(
                                   known[one].to_dict(), f"new {number}")])
                        answer = self._ask_whether(
                            SAME_WORK_AGAIN_PROMPT, asked, report,
                            "same work again")
                        report.setdefault("asked_about_dormant", []).append(
                            {"task": old.id, "states": list(task.members),
                             **answer})
                        same, why = answer["same"], f"asked: {answer['why']}"
                    if same:
                        for state in list(task.members):
                            self._wake(old, live, archive, revived, state,
                                       vectors, places_of, when_of, why,
                                       stamp)
                        joined_to_dormant.append(old.id)
                        continue
                became.append(task)
            live.extend(became)

        # TWO TASKS THAT HAVE BECOME ONE. Each centre inside the other's
        # usual radius is the sign; whether it is the same work is asked,
        # and a pair answered "no" is not asked again.
        joined: List[Dict[str, Any]] = []
        if not resuming:
            pairs = []
            for i, one in enumerate(live):
                for other in live[i + 1:]:
                    if not one.total or not other.total:
                        continue
                    apart = 1.0 - _cosine_of(one.centre, other.centre)
                    if apart <= min(one.radius, other.radius):
                        said = set(one.profile.get("not_the_same") or [])
                        if other.id not in said:
                            pairs.append((apart, one, other))
            pairs.sort(key=lambda row: row[0])
            for apart, one, other in pairs[:self.ASKS_PER_ROUND]:
                if one.retired_at or other.retired_at:
                    continue
                asked = "\n".join(["TASK A"] + self._task_said(one) + [""]
                                   + ["TASK B"] + self._task_said(other))
                answer = self._ask_whether(SAME_TASK_PROMPT, asked, report,
                                           "same task")
                joined.append({"tasks": [one.id, other.id],
                               "apart": round(apart, 4), **answer})
                if answer["same"]:
                    keep_it, drop = ((one, other) if one.seat <= other.seat
                                     or other.seat < 0 else (other, one))
                    keep_it.absorb(drop)
                    keep_it.happened = {"kind": "joined",
                                        "from": [drop.id],
                                        "gained_ids": list(drop.members)}
                    drop.retired_at = stamp
                    drop.born_of = dict(drop.born_of,
                                        joined_into=keep_it.id)
                    kept.save([drop])
                    live.remove(drop)
                else:
                    for first, second in ((one, other), (other, one)):
                        said = list(first.profile.get("not_the_same") or [])
                        first.profile["not_the_same"] = said + [second.id]

        # PROJECTS: tasks that are parts of one larger piece of work.
        if not resuming and where.scope != SANDBOX:
            self._undo_old_merges(live, kept, report, where)

        projects: List[_Project] = []
        if not resuming:
            projects = self._find_projects(live, archive, known, places_of,
                                           report, where)
        else:
            projects = _Projects(self.db, where.scope).all()
            self._project_of = {one: project.id for project in projects
                                for one in project.tasks}
            report["projects"] = [project.to_dict() for project in projects
                                  if project.tasks]

        # Write it all down, and move the line up to the newest state put
        # in a task or the pile.
        placed_now = {one for task in live + archive for one in task.members}
        # EVERY STATE NOW IN A TASK LEAVES THE PILE - including the ones
        # that have just become a task of their own. Only the leftovers
        # were let go of, so the states a new task was made from stayed in
        # the pile, and the next round made the same task from them again:
        # one more copy every round.
        kept.forget_loose([one for one in kept.loose() if one in placed_now])
        waiting = [one for one in waiting if one not in placed_now]
        kept.set_loose(waiting)
        self._seat_them(live)
        live.sort(key=lambda task: (task.seat, task.created_at))
        for number, task in enumerate(live, start=1):
            task.number = number
        kept.save(live)
        kept.save([task for task in archive
                   if task.id in set(went_quiet) or task.archived_at])
        # THE LINE MOVES UP to where this round began reading - unless it
        # stopped at the cap, in which case to the newest state it read, and
        # the rest come next round.
        if not resuming:
            if (scope or {}).get("at_the_cap"):
                fresh_ids = self._fresh_ids.get(where.scope) or set()
                newest = max((_utc(getattr(known[one], "updated_at", None))
                              for one in fresh_ids if one in known),
                             default="")
                newest = f"{newest}@cap" if newest else ""
            else:
                newest = str(report.get("reads_until") or "")
            self._processed_to(where, newest)

        new_states = set(placed.get("placed") or {})
        moved: Dict[str, Dict[str, List[str]]] = {}
        for state, task_id in (placed.get("placed") or {}).items():
            moved.setdefault(str(task_id), {"gained": [], "lost": []})
            moved[str(task_id)]["gained"].append(str(state))
        for row in revived:
            moved.setdefault(row["task"], {"gained": [], "lost": []})
            moved[row["task"]]["gained"].append(row["state"])
            new_states.add(row["state"])
        for task in live:
            got = list((task.happened or {}).get("gained_ids") or [])
            if got:
                moved.setdefault(task.id, {"gained": [], "lost": []})
                moved[task.id]["gained"].extend(got)
        report["moved"] = {key: {"gained": sorted(set(value["gained"])),
                                 "lost": sorted(set(value["lost"]))}
                           for key, value in moved.items()
                           if value["gained"] or value["lost"]}

        report["tree"] = kept.last_tree()
        report["similarity"] = {
            "source": source["how"], "model": source.get("model", ""),
            "read_ms": source.get("ms", 0),
            "unavailable": source.get("unavailable", ""),
            "pairs_scored": len(arrivals) * max(1, len(live)),
            "scored_now": False,
            "why": (f"{len(arrivals)} new state(s), each against every "
                    f"task's centre and places. Every pair was not scored: "
                    f"that is the square of the calendar, and nothing is "
                    f"regrouped - a task keeps what it needs of its old "
                    f"states."),
            "pairs": []}
        within = self._within_held(live, vectors, known)
        drawn = [self._drawn(task, nodes, within[index], index, new_states)
                 for index, task in enumerate(live)]
        # The pieces each task was just found to have, kept for next round.
        kept.save(live)
        report["loose"] = list(waiting)
        report["archived"] = len(archive)
        report["continuity"] = {
            "kept_tasks": len(live),
            "arrivals": len(arrivals),
            "placed": len(placed.get("placed") or {}),
            "loose": len(loose),
            "waiting": len(waiting),
            "became_tasks": [task.id for task in became],
            "picked_up_again": revived,
            "joined_to_dormant": joined_to_dormant,
            "went_dormant": went_quiet,
            "joined": joined,
            "profiled_now": migrated,
            "ambiguous": int(placed.get("ambiguous") or 0),
            "evidence": placed.get("evidence") or {},
            "forgotten": len(stale),
            "ended": [],
            "since_rebuild": 0,
            "window": dict(scope),
            "processed": sorted(arrivals),
            "rebuilt": False,
            "why": (f"{len(placed.get('placed') or {})} of {len(arrivals)} new "
                    f"state(s) joined a task; {len(loose)} fitted none"
                    + (f"; {len(revived)} picked a dormant task up again"
                       if revived else "")
                    + (f"; {len(became)} new task(s) from what fitted nowhere"
                       if became else "")
                    + (f"; {len(went_quiet)} task(s) went dormant"
                       if went_quiet else "")
                    + ". Nothing is regrouped."),
            "signals": [],
            "lineage": [],
            "limits": {"accept_at": self._accept_at(),
                       "far": _FAR, "place_counts": _PLACE_COUNTS,
                       "tie_within": _TIE_WITHIN,
                       "revive_at": REVIVE_AT,
                       "archive_after_days": ARCHIVE_AFTER_DAYS,
                       "loose_kept_days": LOOSE_KEPT_DAYS},
        }
        kept.record({"id": report.get("trace_id") or f"run-{task_now()}",
                     "ran_at": task_now(), "states": len(nodes),
                     "arrivals": len(arrivals),
                     "placed": len(placed.get("placed") or {}),
                     "loose": len(loose),
                     "ambiguous": int(placed.get("ambiguous") or 0),
                     "rebuilt": False, "signals": [], "lineage": [],
                     "tree": {}, "seen_from": scope.get("from", ""),
                     "shape": self._shape()})
        report["layers"] = drawn
        seat_of = {node.id: index for index, node in enumerate(nodes)}
        groups = [[seat_of[one] for one in task["states"] if one in seat_of]
                  for task in drawn]
        return drawn, groups

    #: How much of a task's recent states have to touch a place for it to
    #: count as one of the task's own places when looking for projects.
    STRONG_PLACE_AT = 0.2

    def _home_of(self, task: Task, folders: Dict[str, str],
                 share: Dict[str, float]) -> str:
        """Where a task's work lives: its project folder, or the part of a
        site that names the work - from the task's own description of where
        it lives, and failing that from the places its states touch."""
        where = str((task.described or {}).get("where") or "")
        found: List[str] = []
        if where:
            read = _workspace([], [where])
            for key in list(read.get("files") or []) + list(
                    read.get("urls") or []):
                key = _canonical_place(key)
                if key and not _not_a_place(key, self._own_folder()):
                    found.append(key)
            for word in _FOLDER_WORD.findall(_canonical_place(where)):
                if word in folders:
                    found.append(folders[word])
        if not found:
            found = [place for place, part in sorted(
                share.items(), key=lambda pair: -pair[1])
                if part >= 0.3 and (_is_a_path(place) or "/" in place)]
        homes = [_project_root(key, folders) for key in found]
        homes = [one for one in homes if one]
        if not homes:
            return ""
        # The one named most, and the shortest of those: the folder the
        # files are in rather than one of the files.
        counted: Dict[str, int] = {}
        for one in homes:
            counted[one] = counted.get(one, 0) + 1
        return sorted(counted, key=lambda one: (-counted[one], len(one)))[0]

    def _own_folder(self) -> str:
        return _canonical_place(str(self.config.get("agent_root") or ""))

    def _declined_groups(self, where: _Where) -> List[List[str]]:
        if where.scope == SANDBOX:
            return list(self._marks.get("declined") or [])
        try:
            return json.loads(str(self.config.get(
                "state_projects_declined", "[]") or "[]"))
        except ValueError:
            return []

    def _decline_group(self, where: _Where, ids: Sequence[str],
                       counts: Optional[Dict[str, int]] = None) -> None:
        held = self._declined_groups(where)
        held.append({"ids": sorted(ids), "counts": dict(counts or {})})
        held = held[-200:]
        if where.scope == SANDBOX:
            self._marks["declined"] = held
            return
        self.config.set("state_projects_declined", json.dumps(held),
                        internal=True)

    def _find_projects(self, live: List[Task], archive: List[Task],
                       known: Dict[str, _Node],
                       places_of: Dict[str, List[str]],
                       report: Dict[str, Any], where: _Where
                       ) -> List[_Project]:
        """Tasks that are parts of one larger piece of work, under it.

        A task's home is where its work lives. Tasks that share a home, or
        share a place that is a large part of each of them, may be one
        project - and whether they are is asked, never assumed: a mailbox
        is where many unrelated things happen. What the model groups is
        kept; a grouping it turned down is not asked about again.
        """
        store = _Projects(self.db, where.scope)
        projects = store.all()
        everyone = {task.id: task for task in live + archive}
        # A TASK THAT WAS JOINED INTO ANOTHER passes its project on - the
        # work it held is in the task that took it in. And a task that
        # ended for good is not part of anything any more.
        ended = {task.id: task for task in Kept(self.db, where.scope).all(500)
                 if task.retired_at}
        for project in projects:
            kept_tasks: List[str] = []
            for one in project.tasks:
                if one in everyone:
                    kept_tasks.append(one)
                    continue
                into = str((getattr(ended.get(one), "born_of", None) or {})
                           .get("joined_into") or "")
                if into in everyone and into not in kept_tasks:
                    kept_tasks.append(into)
            kept_tasks = list(dict.fromkeys(kept_tasks))
            if kept_tasks != project.tasks:
                project.tasks = kept_tasks
                store.save(project)
        # A TASK FOUND AGAIN when the grouping was re-read - after a
        # setting changed - is in the project the tasks it came from were
        # in. Those were asked about; asking again about the same work
        # under a new id would be asking the same question twice.
        belongs = {one: project for project in projects
                   for one in project.tasks}
        # BUT NOT A TASK TAKEN OUT AS OTHER WORK. Split back out of a merge
        # or found inside a group, it left that task because the model said
        # it was different work - the last thing it should be given is the
        # project of the task it was taken out of. One that already was is
        # taken back out of it.
        def other_work(task: Task) -> bool:
            born = task.born_of or {}
            return bool(born.get("other_work") or born.get("was")
                        or str(born.get("why") or "").startswith(
                            "the model found it inside"))

        for project in projects:
            parent = set(project.tasks)
            mistaken = [one for one in project.tasks if one in everyone
                        and other_work(everyone[one])
                        and set((everyone[one].born_of or {}).get("from")
                                or []) & parent]
            if mistaken:
                project.tasks = [one for one in project.tasks
                                 if one not in mistaken]
                store.save(project)
        belongs = {one: project for project in projects
                   for one in project.tasks}
        for task in live:
            if task.id in belongs or other_work(task):
                continue
            came_from = [one for one in (task.born_of or {}).get("from") or []]
            homes = {belongs[one].id for one in came_from if one in belongs}
            if came_from and len(homes) == 1 and all(
                    one in belongs for one in came_from):
                project = belongs[came_from[0]]
                project.tasks.append(task.id)
                belongs[task.id] = project
                store.save(project)
        share: Dict[str, Dict[str, float]] = {}
        folders: Dict[str, str] = {}
        for keys in places_of.values():
            for key in keys:
                if _is_a_path(key):
                    name = key.rsplit("/", 1)[-1]
                    if len(name) >= 8 and ("_" in name or "-" in name):
                        folders.setdefault(name, key)
        # A task the model said is a way of working rather than work is not
        # offered to a project: it has already been judged not to be work.
        live = [task for task in live
                if (task.described or {}).get("concrete") is not False]
        for task in live:
            held = [one for one in task.members if one in known]
            counted: Dict[str, int] = {}
            for one in held:
                for place in set(places_of.get(one, ())):
                    counted[place] = counted.get(place, 0) + 1
            share[task.id] = {place: count / max(1, len(held))
                              for place, count in counted.items()}
        home = {task.id: self._home_of(task, folders, share.get(task.id, {}))
                for task in live}
        specific = _specificity(live + archive)
        strong_of = {task.id: [place for place, part in share[task.id].items()
                               if part >= self.STRONG_PLACE_AT]
                     for task in live}

        # WORK SOMEWHERE ELSE, TAKEN BACK OUT. The same rule as for a task's
        # states (`tasks.apart_within`): tasks in a project whose home the
        # rest of it never has, and that read as other work, leave it - and
        # are not offered to it again until they have grown. They are free
        # to be found as a project of their own below.
        #
        # By HOME alone: a home is a project's folder or site, telling by
        # what it is - and how many tasks share it is the point here, not a
        # reason to discount it, as it is for a state's places.
        for project in projects:
            inside = [one for one in project.tasks if one in home]
            found = apart_within(
                inside, {one: [home[one]] if home[one] else []
                         for one in inside},
                {one: everyone[one].centre for one in inside
                 if everyone[one].total},
                {}, min_nodes=2)
            if not found:
                continue
            leaving = set(found["states"])
            project.tasks = [one for one in project.tasks if one not in leaving]
            for one in leaving:
                project.declined[one] = everyone[one].count
            store.save(project)
            report.setdefault("taken_out_of_projects", []).append(
                {"project": project.id, "place": found["place"],
                 "tasks": sorted(leaving)})
        belongs = {one: project for project in projects
                   for one in project.tasks}

        # Linked: the same home, or a place strong in both - but NOT a
        # common place between two tasks whose homes differ. Two pieces of
        # work in different folders, both done with the mail open, are not
        # one project because of the mail (the same rule as `tasks.fits`).
        parent = {task.id: task.id for task in live}

        def root(one: str) -> str:
            while parent[one] != one:
                parent[one] = parent[parent[one]]
                one = parent[one]
            return one

        for index, one in enumerate(live):
            for other in live[index + 1:]:
                same_home = home[one.id] and home[one.id] == home[other.id]
                both = set(strong_of[one.id]) & set(strong_of[other.id])
                apart = (home[one.id] and home[other.id]
                         and home[one.id] != home[other.id])
                if apart:
                    both = {place for place in both
                            if specific.get(place, 1.0) >= _TELLING}
                if same_home or both:
                    parent[root(one.id)] = root(other.id)
        # AND A TASK WHOSE OWN STATES ARE IN A PROJECT'S PLACES, even before
        # it has a name to say where it lives: a task made from the pile
        # this round is linked to the project its states are in.
        for project in projects:
            members = [one for one in project.tasks if one in parent]
            if not members:
                continue
            for task in live:
                if task.id in project.tasks or task.id not in parent:
                    continue
                held = [one for one in task.members if one in known]
                inside = sum(1 for one in held
                             if project.holds(places_of.get(one, ())))
                if held and inside / len(held) >= 0.3:
                    parent[root(task.id)] = root(members[0])
        groups: Dict[str, List[Task]] = {}
        for task in live:
            groups.setdefault(root(task.id), []).append(task)
        # A grouping turned down is asked about again only once one of its
        # tasks has grown by a third: it may be other work by then.
        counts = {task.id: task.count for task in live}
        linked = sum(1 for members in groups.values() if len(members) >= 2)
        self._at(report, "looking for projects",
                 f"{sum(len(members) for members in groups.values() if len(members) >= 2)}"
                 f" task(s) in {linked} group(s) share where their work lives")
        declined = []
        for old in self._declined_groups(where):
            ids = old.get("ids", []) if isinstance(old, dict) else list(old)
            then = old.get("counts", {}) if isinstance(old, dict) else {}
            grown = any(counts.get(one, 0) >= 1.5 * int(then.get(one) or 0)
                        and then.get(one) for one in ids)
            if not grown:
                declined.append(set(ids))
        asked = 0
        for members in groups.values():
            if len(members) < 2 or asked >= self.ASKS_PER_ROUND:
                continue
            ids = {task.id for task in members}
            existing = next((project for project in projects
                             if ids & set(project.tasks)), None)
            if existing is not None:
                newcomers = [task for task in members
                             if task.id not in existing.tasks
                             and not existing.turned_down(task)]
                if not newcomers:
                    continue
                asked += 1
                self._at(report, "asking whether tasks belong to a project",
                         f"{len(newcomers)} task(s) and {existing.name}")
                lines = [f"THE PROJECT: {existing.name}",
                         f"why it is one: {existing.why}",
                         f"where it lives: {existing.home or '(not said)'}",
                         "its tasks:"]
                lines += [f"  - {_a_real_name(everyone[one].described or {}) or '(not named)'}"
                          for one in existing.tasks if one in everyone]
                lines += ["", "TASKS THAT NOW SHARE ITS PLACES:"]
                for number, task in enumerate(newcomers, start=1):
                    lines += [f"--- {number} ---"] + self._task_said(task) + [
                        f"where: {str((task.described or {}).get('where') or '')[:400]}"]
                answer = self._ask_project(JOINS_PROJECT_PROMPT,
                                           "\n".join(lines), report)
                chosen = {number for number in answer.get("tasks") or []
                          if isinstance(number, int)}
                for number, task in enumerate(newcomers, start=1):
                    if number in chosen:
                        existing.tasks.append(task.id)
                        existing.declined.pop(task.id, None)
                    else:
                        existing.declined[task.id] = task.count
                store.save(existing)
                report.setdefault("asked_about_projects", []).append(
                    {"project": existing.id, "asked": [task.id for task in newcomers],
                     **answer})
                continue
            if any(ids <= set(old) for old in declined):
                continue
            asked += 1
            self._at(report, "asking whether these tasks are one project",
                     f"{len(members)} tasks that share where their work lives")
            lines = []
            for number, task in enumerate(members, start=1):
                lines += [f"--- {number} ---"] + self._task_said(task) + [
                    f"where: {str((task.described or {}).get('where') or '')[:400]}",
                    f"home: {home.get(task.id) or '(none found)'}", ""]
            answer = self._ask_project(ONE_PROJECT_PROMPT, "\n".join(lines),
                                       report)
            report.setdefault("asked_about_projects", []).append(
                {"asked": sorted(ids), **answer})
            chosen = [members[number - 1] for number in answer.get("tasks") or []
                      if isinstance(number, int) and 1 <= number <= len(members)]
            if answer.get("one_project") and len(chosen) >= 2:
                homes = [home.get(task.id) for task in chosen if home.get(task.id)]
                common = max(set(homes), key=homes.count) if homes else ""
                shared = sorted(set.intersection(*[
                    {place for place, part in share[task.id].items()
                     if part >= self.STRONG_PLACE_AT} for task in chosen])
                    if chosen else set())
                project = _Project(
                    id=f"project-{where.scope}-{uuid.uuid4().hex[:10]}",
                    name=str(answer.get("name") or "").strip()[:120],
                    why=str(answer.get("why") or "").strip()[:500],
                    home=common, tasks=[task.id for task in chosen],
                    places=shared[:20],
                    declined={task.id: task.count for task in members
                              if task not in chosen},
                    described={"named_size": len(chosen),
                               "summarised": [task.id for task in chosen]})
                store.save(project)
                projects.append(project)
            else:
                self._decline_group(where, sorted(ids),
                                    {task.id: task.count for task in members})
        self._project_of = {one: project.id for project in projects
                            for one in project.tasks}
        report["projects"] = self._projects_drawn(projects, everyone)
        return projects

    def _projects_drawn(self, projects: Sequence[_Project],
                        tasks: Dict[str, Task]) -> List[Dict[str, Any]]:
        """Each project as the page draws it: as a task is drawn, from its
        tasks - when it was worked on, how much, and how urgent and
        important its most pressing part is."""
        out = []
        leisure_ids = self._leisure_tasks()
        for project in projects:
            members = [tasks[one] for one in project.tasks if one in tasks]
            if not members:
                continue
            row = project.to_dict()
            stretches: List[List[str]] = []
            for task in members:
                stretches.extend(task.stretches())
            stretches.sort(key=lambda pair: pair[0])
            joined: List[List[str]] = []
            for start, end in stretches:
                if joined and start <= joined[-1][1]:
                    joined[-1][1] = max(joined[-1][1], end)
                else:
                    joined.append([start, end])
            weights = {}
            for part in ("urgency", "importance"):
                values = [_a_number_between((task.described or {}).get(part),
                                            1, 7) for task in members]
                values = [one for one in values if one is not None]
                weights[part] = max(values) if values else None
            phases = [task.phase() for task in members]
            row.update({
                "stretches": joined,
                "began": min((task.first_seen for task in members
                              if task.first_seen), default=""),
                "ended": max((task.last_seen for task in members
                              if task.last_seen), default=""),
                "count": sum(task.count for task in members),
                "urgency": weights["urgency"],
                "importance": weights["importance"],
                "phase": ("active" if "active" in phases else
                          "quiet" if "quiet" in phases else "archived"),
                # Hidden when every task in it is: a project of ways of
                # working is not a piece of work either.
                "hidden": all((task.described or {}).get("concrete") is False
                              for task in members),
                # Of leisure when every task in it is.
                "leisure": all(task.id in leisure_ids for task in members),
                "status_at": str(project.described.get("status_at") or ""),
                "written_by": dict(project.described.get("written_by") or {}),
            })
            out.append(row)
        return out

    def _bring_projects_up_to_date(self, report: Dict[str, Any],
                                   where: _Where) -> None:
        """Each project's status, after its tasks have theirs - and only
        where one of its tasks moved this round, or it has none yet.

        The most urgent project first. Asked once per round per project,
        like everything else in a round; one whose tasks have changed a
        good deal since it was named is asked about its name too.
        """
        store = _Projects(self.db, where.scope)
        projects = [one for one in store.all() if one.tasks]
        if not projects or self.models is None:
            return
        tasks = {task.id: task for task in Kept(self.db, where.scope).live()
                 + Kept(self.db, where.scope).archived()}
        moved = set((report.get("moved") or {}).keys())
        moved |= {str(one.get("task") or "") for one in
                  report.get("status_calls") or [] if one.get("changed")}
        # Named this round, by its stored id - which is what the round
        # writes down as it names each one.
        written = set(self._named_this_round())

        def pressing(project: _Project) -> int:
            values = [_a_number_between((tasks[one].described or {}).get(
                "urgency"), 1, 7) or 0 for one in project.tasks if one in tasks]
            return -(max(values) if values else 0)

        asked = set(self._tried_this_round())

        # SEVERAL AT ONCE, WHEN THE COMPUTER HAS ROOM: which project is next is
        # decided here, the most pressing first; the questions run beside
        # each other as `self.room` allows; each answer is written down here
        # as it comes back.
        def _to_ask():
            for project in sorted(projects, key=pressing):
                if not self._going(where):
                    return
                key = f"project:{project.id}"
                if key in asked:
                    continue
                members = [tasks[one] for one in project.tasks if one in tasks]
                if not members:
                    continue
                has_one = bool(project.described.get("status"))
                touched = any(task.id in moved or task.id in written
                              for task in members)
                # NAMED AGAIN WHEN ITS TASKS ARE LARGELY OTHERS - come or
                # gone - not only when there are more of them: a project
                # that lost a third of its tasks, or swapped them, is
                # described by a name for work it no longer holds.
                named_size = int(project.described.get("named_size") or 0)
                named_from = project.described.get("summarised")
                if isinstance(named_from, list) and named_from:
                    churn = len(set(named_from) ^ {task.id for task in members}) \
                        / len(named_from)
                    rename = churn >= self._resummarise_after()
                else:
                    rename = bool(named_size) and abs(len(members) - named_size) \
                        >= max(1, named_size / 3)
                # AND WHEN THE USER CHANGED ITS NEXT STEPS, even if
                # nothing else moved: the change is told to the next call,
                # and a round that skipped the project would leave it
                # waiting for some unrelated work to happen in it first.
                todo_due = self._todo_due("state_projects", where.scope,
                                          project.id)
                if has_one and not touched and not rename and not todo_due:
                    continue
                report["summarising"] = key
                self._at(report, "saying where a project stands",
                         f"{project.name}, {len(members)} tasks")
                lines = [f"THE PROJECT: {project.name}",
                         f"why it is one: {project.why}",
                         f"where it lives: {project.home or '(not said)'}",
                         f"where it stood: {project.described.get('status') or '(not said yet)'}",
                         "", "ITS TASKS:"]
                for task in members:
                    held = task.described or {}
                    lines += [f"- {_a_real_name(held) or '(not named)'} "
                              f"(urgency {held.get('urgency', '?')}, importance "
                              f"{held.get('importance', '?')}, {task.phase()})",
                              "    most recently:"]
                    # WHAT IS HAPPENING IN IT, not what it was last described
                    # as: a task in a project is not described again, so its
                    # own status would only ever get older.
                    try:
                        recent = sorted(where.states.by_ids(list(task.members[-6:])),
                                        key=lambda s: str(getattr(s, "starts_at", "")))
                    except Exception:    # pragma: no cover - a read
                        recent = []
                    lines += [f"      {str(getattr(s, 'starts_at', ''))[:16]}  {s.title}"
                              for s in recent] or ["      (nothing)"]
                # ITS NEXT STEPS, and what the user did to them. A project
                # is always at the top, so it always has a list to keep.
                shown = self._todo_asking("state_projects", where.scope,
                                          project.id)
                if shown is not None:
                    lines += ["", _todo.told(shown)]
                prompt = PROJECT_STATUS_PROMPT.replace(
                    "{in_english}", PROJECT_ENGLISH
                    if needs_english_copy(self.config) else "").replace(
                    "{rename}", RENAME_NOTE if rename else "").replace(
                    "{todo}", _todo.rules("project") + "\n\n"
                    if shown is not None else "")
                self._also_tried(key)
                yield (project, key, members, rename, prompt,
                       "\n".join(lines), shown)

        def _land(job, answer, error):
            project, key, members, rename, _prompt, _asked, shown = job
            if error is not None:
                # NOT SHOWN, SO NOT CLEARED: the user's changes wait for
                # the next call that gets an answer.
                self._land_todo("state_projects", where.scope, project,
                                shown, None, failed=True)
                raise error
            self._spent(report, answer.pop("used", None) or {})
            status = _without_ids(str(answer.get("status") or "").strip())
            if status:
                project.described["status"] = status[:600]
            english = answer.get("in_english")
            if isinstance(english, dict):
                kept = {part: _without_ids(str(english.get(part) or "").strip())[:600]
                        for part in ("name", "status") if str(english.get(part) or "").strip()}
                if kept:
                    from .english_copies import stamped
                    project.described["in_english"] = stamped(
                        dict(project.described.get("in_english") or {}, **kept),
                        dict(project.described, name=project.name), kept.keys())
                project.described["status_at"] = task_now()
                project.described["written_by"] = answer.get("written_by") or {}
                self._also_named(key)
            if rename and str(answer.get("name") or "").strip():
                project.name = str(answer["name"]).strip()[:120]
                project.described["named_size"] = len(members)
                project.described["summarised"] = [task.id for task in members]
            store.save(project)
            # AN ANSWER CLEARS WHAT IT WAS SHOWN, list or no list; a call
            # that never reached the model clears nothing.
            self._land_todo("state_projects", where.scope, project, shown,
                            _todo.read_answer(answer),
                            str(answer.get("todo_note") or ""),
                            failed="answer" not in answer)
            report.setdefault("project_statuses", []).append(
                {"project": project.id, "status": status,
                 "renamed": rename, "why": answer.get("why", "")})
            return True

        at_once(_to_ask(),
                lambda job: self._ask_project(
                    job[4], job[5], report, counted=False,
                    most=900 if job[6] is not None else 400),
                self.room, going=lambda: self._going(where), landed=_land,
                pool=self.pool, kind="grouping",
                told=lambda running, allowed, why: self._say_at_once(
                    report, running, allowed, why))
        # The page reads the projects off the report; give it the new words.
        report["projects"] = self._projects_drawn(
            [one for one in store.all() if one.tasks], tasks)

    def _ask_project(self, prompt: str, asked: str,
                     report: Dict[str, Any],
                     counted: bool = True, most: int = 400) -> Dict[str, Any]:
        """One question about whether tasks are one larger piece of work."""
        out: Dict[str, Any] = {"prompt": asked}
        if self.models is None:
            out["why"] = "no model to ask"
            return out
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": asked}],
                             system=prompt, max_tokens=most,
                             task="state_heartbeat",
                             # A PROJECT IS A TOP-LEVEL TASK - their aggregate,
                             # the same to the user and to the program.
                             language=in_display("its name, status, next steps and why")
                             + in_english('the "in_english" copy, when it is asked for'),
                             metadata={"kind": "projects"}),
                role="heartbeat")
        except Exception as exc:
            out["why"] = f"could not ask: {exc}"[:300]
            return out
        reply = getattr(response, "text", "") or ""
        answered = _as_json(reply) or {}
        used = {"calls": 1, "tokens_in": int(getattr(getattr(
            response, "usage", None), "input_tokens", 0) or 0),
            "tokens_out": int(getattr(getattr(
                response, "usage", None), "output_tokens", 0) or 0)}
        # Counted by the caller when it runs beside others: the round's
        # account is written on one thread.
        if counted:
            self._spent(report, used)
        else:
            out["used"] = used
        out.update(answered)
        out["answer"] = reply
        out["written_by"] = _writer(response)
        return out

    def _wake(self, task: Task, live: List[Task], archive: List[Task],
              revived: List[Dict[str, Any]], state: str,
              vectors: Dict[str, List[float]],
              places_of: Dict[str, List[str]],
              when_of: Dict[str, Tuple[str, str]], why: str,
              stamp: str) -> None:
        """A dormant task picked up again, with the state that did it."""
        if task.archived_at:
            last = _as_moment(task.last_seen)
            idle = ((datetime.now(timezone.utc) - last).days
                    if last is not None else None)
            task.archived_at = ""
            task.happened = {"kind": "picked up again", "why": why,
                             "dormant_days": idle}
            if task in archive:
                archive.remove(task)
            if task not in live:
                live.append(task)
        began, ended = when_of.get(state, ("", ""))
        task.take(state, vectors[state], places_of.get(state, ()), began, ended)
        revived.append({"task": task.id, "state": state, "why": why})

    def _what_this_round_did(self, task_id: str,
                             nodes: Sequence[_Node]) -> Dict[str, Any]:
        evidence = getattr(self, "_evidence_now", None) or {}
        titles = {node.id: node.title for node in nodes}
        joined, nearly = [], []
        for state, said in evidence.items():
            row = {"state": state, "title": titles.get(state, "")[:160],
                   "distance": said.get("distance"),
                   "workspace": said.get("workspace"),
                   "place": said.get("place") or "",
                   "part": said.get("part", -1),
                   "why": said.get("why") or ""}
            if said.get("task") == task_id:
                joined.append(row)
            elif not said.get("task") and said.get("nearest") == task_id:
                nearly.append(row)
        return {"joined": joined, "nearly": nearly}

    def _within_held(self, tasks: Sequence[Task],
                     vectors: Dict[str, List[float]],
                     known: Dict[str, Any]
                     ) -> List[Dict[Tuple[int, int], float]]:
        """The distances inside each task, over the states of it held here."""
        out: List[Dict[Tuple[int, int], float]] = []
        for task in tasks:
            members = [one for one in task.members if one in known
                       and one in vectors]
            distance: Dict[Tuple[int, int], float] = {}
            if len(members) > 1 and self.embedder is not None:
                for one, other, score in self.embedder.pairwise(members,
                                                                vectors):
                    distance[(min(one, other), max(one, other))] = (
                        1.0 - float(score))
            out.append(distance)
        return out

    def _within(self, tasks: Sequence[Task],
                vectors: Dict[str, List[float]]
                ) -> List[Dict[Tuple[int, int], float]]:
        """The distances inside each task, and only inside.

        Each task is scored against itself rather than read off a list of
        every pair in the calendar. That list only exists on a rebuild
        round, and this is needed on every round - but it is also the
        cheaper of the two by a wide margin, because the sum of the
        squares of the parts is far less than the square of the whole: a
        thousand states in eight tasks is sixty-eight thousand pairs
        against half a million.
        """
        out: List[Dict[Tuple[int, int], float]] = []
        for task in tasks:
            members = list(task.members)
            distance: Dict[Tuple[int, int], float] = {}
            if len(members) > 1 and vectors:
                if self.embedder is not None:
                    pairs = self.embedder.pairwise(members, vectors)
                else:                       # pragma: no cover - no embedder
                    pairs = []
                for one, other, score in pairs:
                    distance[(min(one, other), max(one, other))] = (
                        1.0 - float(score))
            out.append(distance)
        return out

    def _drawn(self, task: Task, nodes: Sequence[_Node],
               distance: Dict[Tuple[int, int], float], index: int,
               new_states: Set[str]) -> Dict[str, Any]:
        """One kept task, opened into the tasks inside it.

        The sub-tasks are found within the task's own members rather than
        read off the whole tree. They have to be: the task's membership is
        what persists, and a hierarchy taken from a tree that was built
        over every state would show a division the task no longer has.
        """
        name = f"task_{index + 1}"
        # THE STATES OF IT THIS ROUND HOLDS, which for a task that keeps a
        # profile is its most recent ones - its older states are not read.
        seats = {node.id for node in nodes}
        held_here = [one for one in task.members if one in seats]
        # A task that did not change is not read again. The sub-tasks
        # inside one are found from its own members, which is cheap next
        # to a rebuild and still pointless to repeat every hour for a
        # task that took nothing: at a thousand states in eight tasks it
        # is sixty thousand comparisons an hour to redraw what has not
        # moved.
        fingerprint = (len(held_here), task.version,
                       held_here[-1] if held_here else "")
        held = self._inside.get(task.id)
        if held is not None and held[0] == fingerprint and held[1].get(
                "id_was") == name:
            inside = held[1]
        else:
            inside = self._layers_within(held_here, distance, name)
            inside["id_was"] = name
            self._inside[task.id] = (fingerprint, inside)
        # ITS PIECES' CENTRES, kept on it, so a state that is plainly about
        # one piece can be matched to the task through it next round.
        vectors = getattr(self, "_vectors_now", None) or {}
        if task.profiled and vectors:
            centres = []
            for child in inside.get("children") or []:
                rows = [vectors[one] for one in child.get("states") or []
                        if one in vectors]
                if len(rows) >= 2:
                    centres.append(_unit_vector(
                        [sum(values) for values in zip(*rows)]))
            task.set_parts(centres)
        entry: Dict[str, Any] = {
            "id": name,
            "task_id": task.id,
            # Its place for life, which is what its colour is drawn from -
            # not its row, which moves with every filter and sort.
            "seat": task.seat,
            "depth": 0,
            "size": len(held_here),
            # How many it has ever taken, and how long and how recently it
            # has been worked on - from its profile, not from the states
            # this round happened to read.
            "count": task.count,
            "phase": task.phase(),
            "first_seen": task.first_seen,
            "last_seen": task.last_seen,
            "stretches": task.stretches(),
            "project": (getattr(self, "_project_of", None) or {}).get(task.id, ""),
            "region": task.region(getattr(self, "_specific_now", {}) or {}),
            # WHERE ITS WORK IS, each place with what it is worth - and the
            # weight behind its centre, which levels off near 72 for a task
            # worked on steadily and falls when it is not.
            "places": task.weighed_places(
                getattr(self, "_specific_now", {}) or {}),
            "weight": round(float(task.profile.get("weight") or 0.0), 1),
            # WHAT THIS ROUND DID WITH IT: the states that joined it, and
            # the ones that came nearest to it without joining, each with
            # its distance, what it shared, and why.
            "this_round": self._what_this_round_did(task.id, nodes),
            "height": inside.get("height", 0.0),
            "stands_out": inside.get("stands_out", 0.0),
            "states": list(held_here),
            "loose": list(inside.get("loose") or []),
            "children": (list(inside.get("children") or [])
                         if self._pieces_named() else []),
            "kept": {
                "since": task.created_at,
                "rebuilt_at": task.rebuilt_at,
                "version": task.version,
                "born_of": dict(task.born_of),
                "took": sorted(one for one in task.members
                               if one in new_states),
                "drift": round(task.drift(), 4),
                "widened": round(task.widened(), 3),
                "radius": round(task.radius, 4),
                "spread": round(task.spread, 4),
                "happened": dict(task.happened),
            },
        }
        if inside.get("why_closed"):
            entry["why_closed"] = inside["why_closed"]
        return entry

    def _layers_within(self, members: Sequence[str],
                       distance: Dict[Tuple[int, int], float],
                       name: str) -> Dict[str, Any]:
        """The tree inside one task, read as the tasks inside it.

        Built from the task's own members rather than read off the whole
        tree. It has to be: the membership is what persists, and a
        hierarchy taken from a tree built over every state would show the
        task a division it no longer has.
        """
        order = list(members)
        if len(order) < 2:
            return {"children": [], "loose": [], "height": 0.0,
                    "stands_out": 0.0,
                    "why_closed": "One state. There is nothing inside it."}
        merges = cluster(len(order), distance)
        found = layers(len(order), merges, order,
                       stands_out=self._stands_out(),
                       barely_taller=BARELY_TALLER,
                       worth_opening=self._min_nodes(),
                       enough_children=ENOUGH_CHILDREN,
                       deepest=self._deepest_layers())
        if not found:
            return {"children": [], "loose": [], "height": 0.0,
                    "stands_out": 0.0}
        root = found[0] if len(found) == 1 else {
            "children": found, "loose": [], "height": 0.0, "stands_out": 0.0}
        return {"children": [_renumbered(child, f"{name}.{index + 1}")
                             for index, child in enumerate(
                                 root.get("children") or [])],
                "loose": list(root.get("loose") or []),
                "height": root.get("height", 0.0),
                "stands_out": root.get("stands_out", 0.0),
                "why_closed": root.get("why_closed", "")}

    def _nodes(self) -> List[_Node]:
        """Kept for callers that want the newest states and nothing else."""
        if self.states is None:
            return []
        limit = max(2, int(self.config.get("state_heartbeat_max_states",
                                           MAX_STATES_PER_ROUND)))
        return [_Node(state) for state in self.states.recent(limit=limit)]

    def _reading(self, where: _Where, live: Sequence[Task],
                 watermark: str) -> Tuple[List[_Node], Dict[str, Any]]:
        """Which states this round is about, and where the beginning is.

        THE FIRST ROUND READS A BOUNDED WINDOW - the last month, and at
        most a thousand states - rather than the whole calendar. A user
        who has been running this for a year has thousands of states, and
        a first grouping over all of them is a quarter of a million pairs
        to describe months they are not asking about.

        AND EVERYTHING OLDER IS TREATED AS SEEN. This is the part that is
        easy to get wrong and expensive to get wrong: those states are
        not a backlog waiting to be noticed. If they were left as
        unread, every later round would find four thousand states in no
        task, decide that what fits nowhere has become a task of its own,
        and rebuild on them - forever, because the rebuild would then
        cluster them and find that they are months of unrelated work.
        So a line is drawn and remembered, and what is before it is
        before the beginning.

        Afterwards each round reads from that line forward. When the
        count reaches the cap the line moves up, and the states that fall
        behind it are forgotten by the tasks holding them - which is the
        same mechanism, and the only one: the grouping covers a window
        that moves, and says which window on its own page.
        """
        if where.states is None:
            return [], {"kind": "none", "read": 0}
        cap = self._max_states(where)
        days = self._first_round_days()
        # ONLY WHAT IS NEW, once the tasks keep profiles. A task no longer
        # needs its old states read to know what it is: its centre, its
        # spread and its places are kept on it. So a round reads the states
        # written since the last one - oldest first, at most `cap`, the rest
        # next time - and, so it can draw each task, find the tasks inside
        # it and say where it stands, each task's most recent states and the
        # loose pile.
        marker = self._processed_until(where)
        # Not when a grouping setting has changed: then the grouping is
        # found again once, and that needs the window, not the news.
        shaped = Kept(self.db, where.scope).shape_at_last_rebuild()
        reshaped = bool(shaped) and shaped != self._shape()
        if (live and marker and all(task.profiled for task in live)
                and not reshaped and hasattr(where.states, "after")):
            # "...@cap" when the last round stopped at the cap: the moment
            # it stopped at is read again, for any state written in the
            # same tick as the last one it took.
            at_cap = marker.endswith("@cap")
            moment = _as_moment(marker[:-4] if at_cap else marker)
            fresh = (where.states.after(moment, limit=cap, sources=OBSERVED,
                                        inclusive=at_cap)
                     if moment is not None else [])
            have = {state.id for state in fresh}
            wanted: List[str] = []
            for task in live:
                wanted.extend(task.recent(RECENT_READ))
            wanted.extend(Kept(self.db, where.scope).loose())
            wanted = [one for one in dict.fromkeys(wanted) if one not in have]
            carried = where.states.by_ids(wanted) if wanted else []
            nodes = [_Node(state) for state in list(fresh) + list(carried)]
            self._fresh_ids[where.scope] = {state.id for state in fresh}
            return nodes, {
                "kind": "new since the last round",
                "days": 0, "cap": cap, "read": len(fresh),
                "carried": len(carried),
                "held_outside": 0, "from": marker,
                "at_the_cap": len(fresh) >= cap,
                "states_in_all": len(nodes),
                "before_the_beginning": 0,
                "why": (f"The {len(fresh)} state(s) written since the last "
                        f"round, oldest first"
                        + (f" - the cap of {cap} was reached, and the rest "
                           f"are read next round" if len(fresh) >= cap else "")
                        + f"; and {len(carried)} earlier state(s) read back so "
                          f"each task can be drawn and described: its "
                          f"{RECENT_READ} most recent, and the states that "
                          f"fit no task yet. Older states are not read - each "
                          f"task keeps what it needs of them in its profile."),
            }
        self._fresh_ids.pop(where.scope, None)
        first = not watermark
        # THE MOST RECENT `cap` STATES. Always, and not "the most recent
        # `cap` of those since the line the first round drew".
        #
        # That line was the first version and it quietly froze the window
        # at whatever the cap had been when it was written: raising the
        # setting from four hundred to a thousand changed nothing, because
        # there were only four hundred states after the line and the round
        # dutifully read all four hundred of them. The window crept
        # forward as states arrived and looked as though it had fixed
        # itself, which is worse than plainly not working.
        #
        # The first round is additionally bounded by the day limit,
        # because "what have I been doing" is not a year's question. After
        # that the count is the only bound, and what falls out of it is
        # forgotten by the task that held it - which is the mechanism that
        # was always doing this job.
        began = datetime.now(timezone.utc) - timedelta(days=days)
        # The user's states only: the agent's own work runs are recorded
        # and shown, and are not the user's tasks.
        if first:
            rows = where.states.since(began, limit=cap, sources=OBSERVED,
                                      users_only=True)
        else:
            rows = where.states.recent(limit=cap, sources=OBSERVED,
                                       users_only=True)
        # What a task holds that this window no longer reaches. NOT read
        # back in: that was the first version and it made the cap a
        # decoration, because every member of every task was added back
        # whatever the window said, so nothing ever fell behind the line
        # and the round grew without bound. They are counted here and
        # forgotten by their tasks below.
        seen = {state.id for state in rows}
        held = [one for task in live for one in task.members if one not in seen]
        full = len(rows) >= cap
        # How far back this round reached, which is a RECORD of what was
        # read and not a rule about what may be read next time. Keeping it
        # as a rule is what froze the window.
        oldest = min((str(getattr(state, "updated_at", "") or "")
                      for state in rows), default="")
        line = oldest or began.isoformat()
        total = 0
        try:
            total = sum(int(value) for value
                        in (where.states.counts(sources=OBSERVED)
                            or {}).values())
        except Exception:        # pragma: no cover - a count, not a record
            log.debug("could not count the states", exc_info=True)
        nodes = [_Node(state) for state in rows]
        return nodes, {
            "kind": "first" if first else "since the beginning",
            "days": days if first else 0,
            "cap": cap,
            "read": len(nodes),
            "held_outside": len(held),
            "from": line,
            "at_the_cap": full,
            "states_in_all": total,
            "before_the_beginning": max(0, total - len(nodes)),
            "why": (f"THE FIRST grouping reads the last {days} days, and at "
                    f"most {cap} states, whichever is the smaller - because "
                    f"the question a first grouping answers is what have I "
                    f"been doing, and a year of states is not that question. "
                    f"It is the beginning: anything older than it is before "
                    f"the beginning, and every later round reads only what "
                    f"is written after it."
                    if first else
                    f"The newest {cap} states, which is every state there is "
                    f"unless there are more than {cap} of them. The day limit "
                    f"belonged to the first round and does not apply here."
                    + (f" THE CAP WAS REACHED: the newest {cap} were read and "
                       f"the rest were not. The tasks holding them have let "
                       f"them go, and a state outside this window will not be "
                       f"read while it stays outside. This is the one place "
                       f"the grouping loses something - more than {cap} states "
                       f"arriving between two rounds means the oldest of them "
                       f"are never grouped at all."
                       if full else "")),
        }

    #: How many columns the overview is drawn in.
    #:
    #: Forty across whatever the calendar spans, so a month reads as most
    #: of a day each and a year as most of a week. A fixed bucket width
    #: would give one column for a new install and nine hundred for an old
    #: one, and neither is a picture.
    OVERVIEW_COLUMNS = 40

    def _overview(self, where: _Where, nodes: Sequence[_Node],
                  tasks: Sequence[Dict[str, Any]], window: Dict[str, Any],
                  processed: Sequence[str]) -> Dict[str, Any]:
        """Every state there is, by when it happened, and what this round used.

        The point of this picture is the gap between those two things. A
        round reads a window and groups some of what it reads; the states
        outside the window are never read and the ones inside that match
        no task are not in any. Both of those are invisible in a list of
        tasks, and both matter - the first is information the grouping will
        never see, and the second is states the grouping had nothing to say
        about.

        Drawn from EVERY state in the source, not from the ones this round
        read: `timeline()` has no limit, so the totals here are the folder's
        own count and can be checked against it by hand. A second round
        after some states have been added shows the same columns plus the
        new ones - if it did not, one of the two numbers would be wrong.
        """
        # Only the first round has a duration limit at all. Afterwards the
        # window starts at the line the first round drew and the only
        # limit is the count, so naming the month would be naming a rule
        # that did not apply.
        first_round = str(window.get("kind") or "") == "first"
        stamps = []
        try:
            stamps = [_as_moment(one) for one
                      in (where.states.timeline(sources=OBSERVED) or [])]
        except Exception:        # pragma: no cover - a source without one
            log.debug("could not read the timeline", exc_info=True)
        stamps = [one for one in stamps if one is not None]
        if not stamps:
            return {}

        # Which states this round grouped, and when each of them happened.
        grouped = {one for task in tasks for one in (task.get("states") or [])}
        read_at: Dict[str, Any] = {}
        for node in nodes:
            moment = _as_moment(str(node.starts_at or node.created_at or ""))
            if moment is not None:
                read_at[node.id] = moment

        first, last = min(stamps), max(stamps)
        span = (last - first).total_seconds() or 1.0
        columns = max(1, int(self.OVERVIEW_COLUMNS))
        width = span / columns

        buckets = [{"from": (first + timedelta(seconds=width * index)).isoformat(),
                    "to": (first + timedelta(seconds=width * (index + 1))).isoformat(),
                    "states": 0, "read": 0, "in_tasks": 0, "this_round": 0}
                   for index in range(columns)]

        def slot(moment: Any) -> int:
            offset = (moment - first).total_seconds()
            return min(columns - 1, max(0, int(offset / width)))

        for moment in stamps:
            buckets[slot(moment)]["states"] += 1
        worked_on = set(processed or ())
        for state_id, moment in read_at.items():
            index = slot(moment)
            buckets[index]["read"] += 1
            if state_id in grouped:
                buckets[index]["in_tasks"] += 1
            if state_id in worked_on:
                buckets[index]["this_round"] += 1

        # And the stretch each task actually drew on, so the shading can be
        # read task by task rather than only in total.
        spans = []
        for task in tasks:
            moments = [read_at[one] for one in (task.get("states") or [])
                       if one in read_at]
            if not moments:
                continue
            spans.append({"id": task.get("id"), "task_id": task.get("task_id"),
                          "size": task.get("size", 0),
                          "from": min(moments).isoformat(),
                          "to": max(moments).isoformat()})

        # WHAT DECIDED THE WINDOW, as two edges on the same axis.
        #
        # The columns are only how finely the picture is drawn. What is
        # read is decided by two thresholds, and which of them bound is
        # the thing a reader wants to know: a month back, and at most so
        # many states. Whichever leaves less is the one that cut, and
        # saying which is not the same as showing a shaded region - the
        # region is the consequence, and these are the causes.
        oldest_read = min(read_at.values()) if read_at else None
        days = self._first_round_days()
        by_duration = datetime.now(timezone.utc) - timedelta(days=days)
        cap = self._max_states(where)
        missed = len(stamps) - len(read_at)
        # Read from what EXISTS, not from what was read. Asking whether the
        # oldest state read is inside the limits answers nothing: it always
        # is, by construction. The question is whether anything was left
        # out, and that is a fact about the folder.
        if not read_at:
            bound = "nothing was read"
        elif len(read_at) >= cap:
            bound = (f"the state count. The newest {cap} were read, which "
                     f"reaches back to {oldest_read.isoformat()}; "
                     f"{missed} older state(s) were not read")
        elif first_round and first < by_duration:
            bound = (f"the {days}-day limit. It reaches back to "
                     f"{by_duration.isoformat()}, and there were fewer than "
                     f"{cap} states inside it; {missed} older state(s) were "
                     f"not read")
        elif missed:
            bound = (f"neither limit. {missed} state(s) were not read "
                     f"because they are older than the line this grouping "
                     f"began at, which is not a limit but a beginning")
        else:
            bound = "neither: every state in the folder was read"
        return {
            "from": first.isoformat(),
            "to": last.isoformat(),
            "columns": columns,
            "column_hours": round(width / 3600.0, 2),
            "buckets": buckets,
            "spans": spans,
            "states_in_all": len(stamps),
            "read": len(read_at),
            "in_tasks": len([one for one in read_at if one in grouped]),
            "this_round": len(worked_on),
            "this_round_from": (min(read_at[one] for one in worked_on
                                    if one in read_at).isoformat()
                                if worked_on & set(read_at) else ""),
            "limits": {
                "max_states": cap,
                "max_days": days if first_round else 0,
                "by_duration": by_duration.isoformat() if first_round else "",
                # Where the newest `cap` reach back to, and ONLY when the
                # round used them. An appending round reads that window to
                # find what is new in it and then works on the new ones
                # alone, so marking the window would mark a line the round
                # did not use.
                "by_count": (oldest_read.isoformat()
                             if oldest_read and len(worked_on) >= len(read_at)
                             else ""),
                "first_round": first_round,
                "not_read": missed,
                "bound_by": bound,
                "worked_on": len(worked_on),
                "worked_on_all_of_it": len(worked_on) >= len(read_at),
            },
            "why": ("Every state there is, by when it happened - the "
                    "columns are only how finely it is drawn, forty across "
                    "whatever the calendar spans. The darkest part of each "
                    "column is what THIS round worked on, which on an "
                    "appending round is the handful that arrived and not "
                    "the window it read to find them; under that is what "
                    "earlier rounds put in a task; the rest is in no task "
                    "or was never read. The totals are the folder's own "
                    "count, not this round's."),
        }

    def _vectors(self, nodes: Sequence[_Node], where: _Where
                 ) -> Tuple[Dict[str, List[float]], Dict[str, Any]]:
        """What each state means, as a vector, and what read them.

        Separated from the scoring below because the two costs are
        nothing alike. This is one row per state, and every row the model
        has already read comes back from the database - so a round in
        which nothing was written pays for nothing. The scoring is the
        square of the calendar, and is only paid when the grouping is
        being found again.
        """
        began = time.perf_counter()
        # NOT `available()` FIRST. With the model held for the round that
        # starts it - sixteen to twenty-one seconds - before anything has
        # looked in the cache, and on a round where nothing new was
        # written every vector is already there. `read_states` looks
        # first and starts the model only for what is missing; None is
        # what `available()` saying no used to mean.
        vectors = (self.embedder.read_states(
            nodes, while_ok=lambda: self._going(where))
            if self.embedder is not None else None)
        if vectors is not None:
            return dict(vectors), {
                "how": "embedding", "model": self.embedder.name,
                "ms": int((time.perf_counter() - began) * 1000),
                "read": len(vectors)}
        return {}, {
            "how": "words",
            "ms": int((time.perf_counter() - began) * 1000),
            "unavailable": (self.embedder.status().get("unavailable", "")
                            if self.embedder is not None else "no embedder")}

    def _alike(self, nodes: Sequence[_Node],
               vectors: Dict[str, List[float]]
               ) -> Tuple[Dict[Tuple[int, int], float],
                          List[Dict[str, Any]], int]:
        """How far apart every pair is, and the few worth printing.

        What comes back is the distances the tree is built from, the
        sixty pairs the page shows, and how many there were. NOT a
        described row per pair: at a thousand states that is half a
        million dictionaries of nine fields, two hundred and fifty
        megabytes, and half a million set intersections computed to
        display sixty of them.

        By meaning where there is a model to read it with, and by words
        where there is not. The difference is worth stating on the report
        rather than inferring from the numbers: the same pair of states
        scores 0.95 one way and 0.74 the other, and a reader comparing
        two runs needs to know which they are looking at.

        Only the title and the detail are read, either way. The evidence
        and the sources are paths and URLs - a language model reading
        `C:/Research/.../fetch.py` is reading punctuation - and they were
        dropped from the comparison with them (section 34.3, as amended).
        """
        order = [node.id for node in nodes]
        if vectors and self.embedder is not None:
            pairs = self.embedder.pairwise(order, vectors)
        else:
            pairs = self._alike_by_words(nodes)
        distance = {(i, j): 1.0 - float(score) for i, j, score in pairs}
        # ANCHOR FIRST. What a state was open on is a stronger signal
        # than what it says: a description is the model's paraphrase of
        # a quarter of an hour, while a path is the same string every
        # time or it is absent. Two states on one file are one piece of
        # work whatever either of them says about it.
        #
        # Done to the distances rather than by clustering twice, because
        # average linkage joins the nearest pair first and an anchored
        # pair sits at nought - so every anchor comes out as a subtree
        # before anything else merges. That is the two-phase result in
        # one pass, with the tree, the dendrogram and every reader of
        # them left exactly as they were.
        anchored = self._anchor(nodes, distance)
        # Highest first, so the reader sees the pairs that decided the
        # shape rather than the first sixty in index order - taken with a
        # heap rather than by sorting, because sorting half a million
        # tuples to keep sixty is most of a second for nothing.
        best = heapq.nlargest(PAIRS_IN_REPORT, pairs,
                              key=lambda row: row[2])
        shown = []
        for i, j, score in best:
            left, right = nodes[i], nodes[j]
            shown.append({
                "i": i, "j": j,
                "a": left.id, "b": right.id,
                "a_title": left.title, "b_title": right.title,
                "score": round(float(score), 4),
                "distance": round(1.0 - float(score), 4),
                "shared_said": sorted(left.said & right.said)[:12],
            })
        return distance, shown, len(pairs), anchored

    def _anchor(self, nodes: Sequence[_Node],
                distance: Dict[Tuple[int, int], float]) -> Dict[str, Any]:
        """Join states that were open on the same thing, before clustering.

        Two things happen here, and they are deliberately asymmetric.

        A shared KEY - the same file, the same page - sets the distance to
        nought, so the pair merges before anything else and the anchor
        comes out as a subtree. That is safe because a shared key is
        nearly conclusive.

        A shared ANCESTOR - `Research/AA/BB` and `Research/AA/CC` - only
        SHORTENS the distance, in proportion to how much of the path
        agrees. It cannot say whether that is one task with two parts or
        two papers on one shelf, and nothing in the path can; what it does
        is bring them near enough that the splitter is asked.

        Neither direction ever pushes apart. Sharing no workspace is
        almost no evidence of being different work, because one task spans
        a browser, an editor and a terminal - so there is something to
        subtract here and nothing to add. A state that names nothing takes
        no part at all: absence is not agreement, and letting two states
        with no evidence look alike is what made the earlier two-measure
        design a structural zero.
        """
        held = [node.workspace for node in nodes]
        if not any(_workspace_keys(one) for one in held):
            return {"anchors": [], "by_word": [], "joined": 0,
                    "pulled": 0, "named": 0, "of": len(nodes)}
        found = _workspace_anchor(held, self._anchors_under())
        seats = dict(found["of"])

        # THE SECOND KIND, over the states the first left alone. Work with
        # no file and no page still says "FieldNotes" or "survey-weights"
        # over and over, and an exact rare word is evidence the embedding
        # cannot carry: the vectors are trained to know that "paper" and
        # "manuscript" mean the same, which is the opposite of what is
        # wanted for a proper noun.
        #
        # Only over states the workspace did not already anchor, and never
        # joined to a workspace anchor. Two kinds of evidence chained
        # together is how a chain gets long: a shared file, then a shared
        # word, then another shared file, and three months are one task.
        # They are also different claims, and a reader should be able to
        # see which one put two states together.
        alone = [index for index in range(len(nodes))
                 if seats.get(index) is None
                 or len(found["anchors"][seats[index]]["members"]) < 2]
        by_word = _anchor_by_words(
            [sorted(nodes[index].said) for index in alone],
            self._words_under(), self._words_agree())
        worded = []
        for one in by_word["anchors"]:
            members = [alone[at] for at in one["members"]]
            worded.append({"members": members, "on": one["on"]})
        # Their own seating, numbered past the workspace anchors so the
        # two can never be confused for one another.
        for at, one in enumerate(worded):
            for index in one["members"]:
                seats[index] = len(found["anchors"]) + at
        anchors_all = list(found["anchors"]) + worded
        joined = pulled = 0
        drawn: List[Dict[str, Any]] = []
        for (i, j) in list(distance):
            same = (seats.get(i) is not None
                    and seats.get(i) == seats.get(j)
                    and len(anchors_all[seats[i]]["members"]) > 1)
            if same:
                distance[(i, j)] = 0.0
                joined += 1
                continue
            near = _workspace_pull(found["keys"][i], found["keys"][j])
            if near <= 0:
                continue
            was = distance[(i, j)]
            distance[(i, j)] = was * (1.0 - self._pulls_by() * near)
            pulled += 1
            if len(drawn) < 24:
                drawn.append({
                    "a": nodes[i].id, "b": nodes[j].id,
                    "a_title": nodes[i].title, "b_title": nodes[j].title,
                    "share": round(near, 3),
                    "was": round(was, 3),
                    "now": round(distance[(i, j)], 3),
                    "under": sorted(
                        set(one for key in found["keys"][i]
                            for one in _workspace_ancestors(key))
                        & set(one for key in found["keys"][j]
                              for one in _workspace_ancestors(key)),
                        key=len, reverse=True)[:1],
                })
        # A pair an anchor joined that the embedding never scored is not
        # in the matrix at all, and has to be put there or the anchor is
        # only half applied.
        for one in anchors_all:
            members = one["members"]
            if len(members) < 2:
                continue
            for a_at, a in enumerate(members):
                for b in members[a_at + 1:]:
                    key = (min(a, b), max(a, b))
                    if key not in distance:
                        distance[key] = 0.0
                        joined += 1
        return {
            # The ids as well as the titles, because the page has to say
            # which task each anchored state ended up in - and an anchor
            # that came apart across two tasks would be the one thing
            # this mechanism must not do.
            "anchors": [{"on": one["on"],
                         "kind": "workspace",
                         "states": len(one["members"]),
                         "ids": [nodes[i].id for i in one["members"]],
                         "titles": [nodes[i].title
                                    for i in one["members"]]}
                        for one in found["anchors"]
                        if len(one["members"]) > 1],
            "by_word": [{"on": one["on"],
                         "kind": "words",
                         "states": len(one["members"]),
                         "ids": [nodes[i].id for i in one["members"]],
                         "titles": [nodes[i].title
                                    for i in one["members"]]}
                        for one in worded],
            "words": {
                "rare_under": by_word["ceiling"],
                "must_share": self._words_agree(),
                "pairs": by_word["edges"],
                "refused": by_word["refused"],
                "considered": len(alone),
            },
            # And the pairs a shared ancestor drew nearer, with how much:
            # this is the half that does not decide anything, and seeing
            # it is how the pull is judged.
            "pulls": drawn,
            "joined": joined, "pulled": pulled,
            "named": found["named"], "of": found["states"],
            "why": ("What a state was open on is read before anything is "
                    "scored. Two states on the same file or page are put "
                    "together outright; two under the same folder are "
                    "drawn nearer and left for the splitter to judge; two "
                    "that name nothing are left alone."),
        }

    def _anchors_under(self) -> float:
        return max(0.0, min(0.5, float(self.config.get(
            "state_anchor_rarer_than", ANCHORS_UNDER))))

    def _words_under(self) -> float:
        return max(0.0, min(0.5, float(self.config.get(
            "state_word_rarer_than", WORDS_UNDER))))

    def _words_agree(self) -> int:
        return max(1, int(self.config.get("state_words_agree", WORDS_AGREE)))

    def _pulls_by(self) -> float:
        return max(0.0, min(0.9, float(self.config.get(
            "state_workspace_pull", WORKSPACE_PULL))))

    @staticmethod
    def _alike_by_words(nodes: Sequence[_Node]) -> List[Tuple[int, int, float]]:
        """The fallback: word overlap of the title and the detail.

        Kept because this application's rule is that everything needing
        more than the standard library degrades rather than stops. It is
        a worse measure - one model writes every state in a stable
        vocabulary, so two states picked at random already share a sixth
        of their words - and the report says which one was used.
        """
        out: List[Tuple[int, int, float]] = []
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                score = similarity(nodes[i].said, nodes[j].said)
                if score > 0:
                    out.append((i, j, score))
        return out

    def _how(self) -> Dict[str, Any]:
        """The algorithm, step by step, from the numbers actually in use.

        Written here rather than in the page, because a page that
        describes a method it does not run is a page that will describe
        the old one after the next change.
        """
        # ASKED WITHOUT LOADING ANYTHING.
        #
        # `available()` answers "is there a model" by LOADING it, which
        # on this machine is thirty-five seconds. This paragraph only
        # wants to know which method the page should describe, and it is
        # built on every round - so a round that read no vectors, needed
        # no vectors, and was only finishing names it had already decided
        # still stopped for half a minute to load a model it never used.
        # `status()` answers the same question from what is configured.
        held = (self.embedder.status() if self.embedder is not None else {})
        embedding = bool(held.get("wanted"))
        stands_out = self._stands_out()
        return {
            "steps": [
                (f"1. WHICH STATES. The first grouping reads the last "
                 f"{self._first_round_days()} days and at most "
                 f"{self._max_states()} states - because the question a "
                 f"first grouping answers is what have I been doing, and a "
                 f"year of states is not that question. It is the beginning: "
                 f"what is older is before it. After that each round reads "
                 f"only what was written since the last one - oldest first, "
                 f"at most {self._max_states()}, the rest next time - and, so "
                 f"it can draw each task and say where it stands, each task's "
                 f"{_RECENT_READ} most recent states. Older states are not "
                 f"read again: each task keeps what it needs of them."),
                ("2. WHAT IS READ OF EACH. The title and the detail, and "
                 "nothing else - the evidence and the sources are paths and "
                 "URLs, which are matched by the words in them or not at "
                 "all. States are written in English so that one small model "
                 "can read all of them; the summaries it writes are in "
                 "whatever language the application is set to, because those "
                 "are what the user reads."),
                ("3. HOW ALIKE TWO OF THEM ARE. "
                 + (f"Each state is turned into a vector by "
                    f"{self.embedder.name} - 384 numbers standing for what "
                    f"the sentence means - and two states are as alike as "
                    f"their vectors point the same way, which is the cosine "
                    f"between them, 0 to 1."
                    if embedding else
                    "There is no embedding model on this machine, so two "
                    "states are as alike as their words overlap: shared / "
                    "smaller x sqrt(smaller / bigger). A worse measure, and "
                    "the report says when it was used.")),
                ("4. HOW FAR APART. One minus that. Two states saying the "
                 "same thing are 0.05 apart; two with nothing in common are "
                 "near 1."),
                ("5. THE TREE. The nearest two states are joined, then the "
                 "nearest two of what is left - a joined pair counting as "
                 "one thing, at the average distance of its members to "
                 "whatever it is compared with - until everything is in one "
                 "tree. The height of a join is how far apart its two "
                 "halves were."),
                (f"6. THE COLLAPSE. Every join in that tree is a pair, so a "
                 f"task whose states came together one at a time is a stack "
                 f"of nested pairs where it should be one layer. A join "
                 f"within {BARELY_TALLER:g} of the height of the join "
                 f"beneath it is the same moment of the same assembly, so "
                 f"its children are lifted into this layer rather than "
                 f"nested under it."),
                (f"7. THE SPLIT. A task is opened into sub-tasks only where "
                 f"its own join stands out: at least {stands_out:g} standard "
                 f"deviations above the joins of the layer it heads, "
                 f"counting a single state as a join at 0. That is two "
                 f"different things being put together, and the two are its "
                 f"sub-tasks. A join at the height everything under it "
                 f"happened at is one thing still being assembled, and "
                 f"opening it would show the assembly rather than any parts."),
                (f"8. WHERE IT STOPS. A task is not opened when it holds "
                 f"fewer than {self._min_nodes()} states - a task of three "
                 f"is three states, and the three are shorter than a tree of "
                 f"them - nor when everything under it is a single state, "
                 f"nor when its join does not stand out. Every closed task "
                 f"says which of the three it was."),
                (f"9. AND THE NEXT ROUND DOES NOT DO ANY OF THAT. Steps 5 "
                 f"to 8 are how the first tasks were FOUND, and they are not "
                 f"run again: a grouping computed from nothing each time is "
                 f"right and worthless, because task 3 yesterday and task 3 "
                 f"today would be two unrelated things. Each task keeps a "
                 f"PROFILE instead of every state: a centre that leans "
                 f"towards its recent work (a state's weight halves after "
                 f"{_HALF_LIFE} later states), how many states it has taken, "
                 f"when it was first and last worked on, and the places - "
                 f"files, folders, sites - its states touched, each weighted "
                 f"by how much of the task is there and how few other tasks "
                 f"use it."),
                (f"10. WHERE A NEW STATE GOES. To the task with the lowest "
                 f"distance from its centre, less {_PLACE_COUNTS:g} times how "
                 f"strongly it shares that task's places - if that is at "
                 f"most {_FAR:g}, and if no other task is within "
                 f"{_TIE_WITHIN:g} of it. Otherwise it waits in the pile, and "
                 f"what in the pile groups tightly enough becomes a task of "
                 f"its own. A task's name is written once and kept; what "
                 f"moves as it takes states is where it stands - and the "
                 f"name is written again only when the task's centre has "
                 f"moved {_RENAME_AFTER:g} from where it was when named."),
                (f"11. QUIET, DORMANT, AND BACK. A task nothing has been "
                 f"added to for {_QUIET_DAYS} days is drawn quieter. After "
                 f"{ARCHIVE_AFTER_DAYS} it goes dormant: not drawn, and not "
                 f"ended. Work in its places later - the same folder, the "
                 f"same paper's page - picks it up again, history and name "
                 f"and all; work that only reads like it is asked about. Two "
                 f"tasks whose centres come to sit inside each other are "
                 f"asked about too, and joined if they are one."),
            ],
            "parameters": [
                {"name": "stands out at",
                 "value": f"{stands_out:g} standard deviations",
                 "why": ("The value scipy's own documentation uses for "
                         "cutting a tree by inconsistency. It lands where it "
                         "should here: a join at 0.45 over a layer that "
                         "joined at 0.10 scores above 2, where the steady "
                         "assembly inside one project scores 0. Lower splits "
                         "more, until every slight rise is a sub-task; "
                         "higher gives one flat list again. Every task on "
                         "this page shows its own score, so this can be "
                         "judged rather than guessed.")},
                {"name": "smallest gap that counts",
                 "value": f"{BARELY_TALLER:g} of the distance scale",
                 "why": ("The score is a ratio, so it says out of line for "
                         "differences that are nothing: two clumps joined at "
                         "0.13 whose own joins were at 0.10 and 0.11 score "
                         "1.34 on a gap of two hundredths. This is the floor "
                         "that stops a rounding difference from becoming a "
                         "layer, and the same number decides what counts as "
                         "the same moment in step 5.")},
                {"name": "smallest task worth opening",
                 "value": f"{self._min_nodes()} states",
                 "why": ("Below it a task is shown with its states rather "
                         "than split further. It is the same floor that "
                         "decides what is worth summarising, because it is "
                         "the same judgement: a handful of states is a "
                         "handful of states.")},
                {"name": "a state joins the nearest task within",
                 "value": f"{_FAR:g} of distance, less what the places are worth",
                 "why": ("Measured by replaying six weeks of one person's "
                         "states: the first half built the tasks and the "
                         "second was streamed through in order. The nearest "
                         "centre picked the right task for 81% of them. A "
                         "task's own spread is the wrong yardstick - its "
                         "later states land a median of 5.7 spreads out - so "
                         "the limit is a distance: states that belonged to a "
                         "task sat a median 0.41 from it, states that "
                         "belonged to none 0.48 from the nearest.")},
                {"name": "what a shared place is worth",
                 "value": f"{_PLACE_COUNTS:g} of distance at the most",
                 "why": ("In the same replay, counting the places took right "
                         "placements from 63% to 68% and wrong ones from 33% "
                         "to 31%. A place on many tasks - the mail, a chat "
                         "site - is worth little; one only this task uses is "
                         "worth it all.")},
                {"name": "a tie is left loose within",
                 "value": f"{_TIE_WITHIN:g}",
                 "why": ("A state two tasks would take about equally is not "
                         "guessed at: wrong placements fell from 31% to 28% "
                         "and right ones did not move.")},
                {"name": "a task goes quiet, then dormant, after",
                 "value": f"{_QUIET_DAYS} days, then {ARCHIVE_AFTER_DAYS}",
                 "why": ("Quiet is a mark on a task still drawn. Dormant is "
                         "not drawn, and is kept - so work picked up again "
                         "months later carries on the task it was, rather "
                         "than starting a new one with no history.")},
                {"name": "a name is written again when its task has moved",
                 "value": f"{_RENAME_AFTER:g} from where it was named",
                 "why": ("A task that gained four states is the same task. "
                         "Writing its name again would pay a model call to "
                         "produce the words it already has, and word them "
                         "differently - so a reader would see the name of "
                         "unchanged work change every round. Where it stands "
                         "is updated every round it takes a state.")},
                {"name": "a state no task takes is let go of after",
                 "value": f"{LOOSE_KEPT_DAYS} days",
                 "why": ("It waits in the pile until enough like it arrive to "
                         "be a task of their own. A month on its own, it was "
                         "not the start of anything.")},
                {"name": "most sub-tasks in one layer",
                 "value": f"{ENOUGH_CHILDREN}",
                 "why": ("About what a person reads as a few. Past it the "
                         "layer stops being a summary of what is underneath "
                         "and becomes the list it was meant to replace, so "
                         "the smallest pieces are gathered into one rather "
                         "than shown.")},
            ],
            "reading": (f"{self.embedder.name}, on this machine, English, "
                        f"384 dimensions" if embedding else
                        "word overlap - no embedding model is available"),
            "clustering": (
                "Agglomerative, average linkage, nearest pair first. Average "
                "rather than single, which strings unrelated things into a "
                "chain through one near-duplicate, or complete, which "
                "refuses to join a large group to anything. Built with the "
                "nearest-neighbour chain algorithm: exact, and n-1 joins in "
                "the square of the state count rather than the cube."),
            "layout": (
                "The tree is drawn as a dendrogram: every state a leaf, "
                "every join a bracket at the height the two halves were "
                "apart. The leaves are ordered by the tree rather than by "
                "when the states were written, which is what stops the lines "
                "crossing."),
            "summarised": (
                f"A task is summarised when it holds {self._min_nodes()} "
                f"states or more." if self._summaries_wanted() else
                "Summaries are off. Nothing here was sent to a model."),
        }

    def _group(self, nodes: Sequence[_Node], members: List[int],
               index: int) -> Dict[str, Any]:
        """One community, with everything the reader needs to judge it."""
        min_nodes = self._min_nodes()
        titles = [nodes[i].title for i in members]
        said: Optional[Set[str]] = None
        rests: Optional[Set[str]] = None
        for i in members:
            said = nodes[i].said if said is None else (said & nodes[i].said)
            rests = nodes[i].rests_on if rests is None else (rests & nodes[i].rests_on)
        # The whole files and pages every member names, not the pieces
        # their words were broken into. The pieces are what the grouping
        # ran on and what the index wants; `01749, 023, com, https` is not
        # something to put in front of a reader or a model.
        shared_sources: Optional[Set[str]] = None
        for i in members:
            named = set(nodes[i].sources)
            shared_sources = named if shared_sources is None else (
                shared_sources & named)
        starts = sorted(m for m in (_moment(nodes[i].starts_at) for i in members) if m)
        ends = sorted(m for m in (_moment(nodes[i].ends_at) for i in members) if m)
        return {
            "id": f"group_{index + 1}",
            "size": len(members),
            "big_enough": len(members) >= min_nodes,
            "why_not": ("" if len(members) >= min_nodes else
                        f"{len(members)} state{'' if len(members) == 1 else 's'}, "
                        f"and a group is summarised at {min_nodes}. A summary of "
                        f"a handful says what the handful already says, in a "
                        f"call."),
            "members": [nodes[i].to_dict() for i in members],
            "titles": titles,
            # What every member has in common, which is usually the reason
            # the group exists and is occasionally a single filename that
            # joined two unrelated months.
            "shared_said": sorted(said or ())[:16],
            "shared_rests_on": sorted(rests or ())[:16],
            "shared_sources": sorted(shared_sources or ())[:8],
            "from": starts[0] if starts else None,
            "to": ends[-1] if ends else None,
        }

    def _show_the_prompt(self, report: Optional[Dict[str, Any]], message: str,
                         waiting_for: str = "", searches: int = 0,
                         group: str = "",
                         instruction: Optional[str] = None) -> None:
        """Put the prompt on the page, whole, before the answer arrives.

        WHOLE MEANS BOTH HALVES. A call is a system prompt and a message,
        and the instruction is the half that decides what comes back - the
        output format, what counts as a task, the permission to search.
        Showing only the states and calling it "the whole prompt" was
        worse than showing nothing: it invited the reader to conclude the
        model had never been told any of it, and the token line under it
        read `0 of instruction` because nothing had filled the field in.

        The two are counted apart because they behave differently. The
        instruction is identical on every call of a round and is what a
        cache hit is made of; the message is the states and is different
        every time. One number over the pair would hide which of them a
        round is actually paying for.
        """
        if report is None:
            return
        stages = report.get("stages") or []
        if not stages:
            return
        if instruction is None:
            instruction = self._instruction()
        stage = stages[-1]
        # CARRIED ONCE, not per step. It is the same two thousand tokens on
        # every summary of a round, the page polls this report two or three
        # times a second while a round runs, and a step is one of eight -
        # so a copy on each of them is the largest thing in the payload and
        # all but one copy is the same text. The round already holds it; a
        # step keeps its own only where it differs, which is the call that
        # orders a tech tree and is told something else entirely.
        stage["instruction_tokens"] = estimate_tokens(instruction)
        if instruction != (report.get("instruction") or ""):
            stage["instruction"] = instruction
        stage["prompt"] = message
        stage["message_tokens"] = estimate_tokens(message)
        stage["prompt_tokens"] = (stage["instruction_tokens"]
                                  + stage["message_tokens"])
        if group:
            stage["group"] = group
        if searches:
            stage["searches"] = searches
        stage["waiting_for"] = waiting_for

    def _summarise(self, nodes: Sequence[_Node], group: Dict[str, Any],
                   trace_id: str, where: _Where,
                   vectors: Optional[Dict[str, List[float]]] = None,
                   report: Optional[Dict[str, Any]] = None
                   ) -> Dict[str, Any]:
        """One model call for one group, and the record it writes."""
        out: Dict[str, Any] = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        if self.models is None:
            out["skipped"] = "no model is configured"
            return out
        # Which of them the model is shown, when there are more than it
        # can be shown, and what each one stands for.
        # WHICH STATES GO IN THE PROMPT, decided once for the round.
        #
        # The choice depends on the grouping and the vectors, and both are
        # fixed from the moment a round opens - so making it again on
        # every visit is the same arithmetic for the same answer, and it
        # is the only reason a visit that is merely finishing a round has
        # to read four thousand vectors out of the database first.
        group["sample"] = self._sample_for(group, vectors or {}, where)
        # WHAT THE MODEL WAS SHOWN OF EACH STATE, kept so the panel can
        # show the same and no more. A state in the sample was shown five
        # fields; one the model asked for by name was shown seven; one in
        # neither was shown nothing at all, and was only ever a number in
        # "and 12 more like it". A page that draws all three the same way
        # invites the reader to explain a summary by a field the model
        # never saw.
        group["seen"] = {
            "sampled": [str((one.get("member") or {}).get("id") or "")
                        for one in group["sample"]],
            "in_full": [],
        }
        # The first of what may be several: if the model asks for more, the
        # answer is appended and the whole thing goes again, so `out`
        # ends up holding the last and longest of them.
        prompt = self._prompt(group)
        # And what this task was last described as, if it has been. Shown
        # so the model can send back the part that is now wrong rather
        # than all four - see ALREADY KNOWN in the instruction.
        known = dict(group.get("described") or {})
        if known:
            prompt = prompt + "\n\n" + _already_known(known)
        # AND ITS NEXT STEPS, for a task with nothing above it: the list
        # as it stands and what the user did to it, last, after what is
        # known - and the rules for writing it beside the instruction.
        instruction = self._instruction()
        if group.get("todo") is not None:
            prompt = prompt + "\n\n" + _todo.told(group["todo"])
            instruction = instruction + "\n\n" + _todo.rules("task")
        # ON THE PAGE BEFORE THE CALL, not after it. The prompt is known
        # the moment it is built and the answer takes ten seconds, so
        # holding it back until the answer arrives means the one thing a
        # reader could be reading while they wait is the one thing hidden
        # from them. `report` and what the page polls are the same object,
        # so writing it here is showing it here.
        self._show_the_prompt(report, prompt, group=group.get("id"),
                              waiting_for="the model's answer")
        began = time.perf_counter()
        # A summary may take more than one call, because it may ask before
        # answering. Each ask is answered from THIS GROUP and nothing else,
        # and then the same question is put again with the answer attached.
        out["looks"] = []
        # EVERY CALL, whole: what it was sent, what it said back before
        # anything was read out of it, and what that cost. The fields
        # below this are the parts of the answer that get used - the
        # title, the summary, the evidence - and they are not the answer.
        # A model that replied with prose instead of JSON, or asked to
        # search in a way the parser did not recognise, leaves nothing
        # behind in them at all, and that is exactly the reply somebody
        # needs to read.
        out["exchange"] = []
        asked = prompt
        response = None
        for attempt in range(self.LOOKS_PER_SUMMARY + 1):
            try:
                response = self.models.invoke(
                    ModelRequest(messages=[{"role": "user", "content": asked}],
                                 system=instruction,
                                 max_tokens=int(self.config.get(
                                     "max_output_tokens", 4096)),
                                 task="state_heartbeat",
                                 language=SUMMARY_LANGUAGE,
                                 metadata={"group": group["id"],
                                           "states": group["size"],
                                           "attempt": attempt + 1}),
                    role="heartbeat",
                )
            except ModelUnavailable as exc:
                out["blocked"] = str(exc)
                out["ms"] = int((time.perf_counter() - began) * 1000)
                return out
            out["calls"] = int(out.get("calls", 0)) + 1
            usage = getattr(response, "usage", None)
            self._add_usage(out, usage)
            reply = getattr(response, "text", "") or ""
            answered = _as_json(reply) or {}
            need = answered.get("need")
            out["exchange"].append({
                "call": attempt + 1,
                "instruction": instruction,
                "sent": asked,
                # Unaltered: not trimmed, not parsed, not stripped of ids.
                # The stripping is recorded separately, so the two can be
                # compared and the rule checked rather than trusted.
                "reply": reply,
                "read_as": answered,
                "was_json": bool(answered),
                # WHETHER IT CHOSE TO SEARCH, per call and in so many
                # words. It is a decision the model made, and "no search"
                # and "no record of a search" are not the same fact.
                "asked_to_search": isinstance(need, dict),
                "what_it_asked_for": (
                    {"keywords": list(need.get("keywords") or []),
                     "ids": list(need.get("ids") or [])}
                    if isinstance(need, dict) else None),
                "tokens_in": int(getattr(usage, "input_tokens", 0) or 0),
                "tokens_out": int(getattr(usage, "output_tokens", 0) or 0),
                "tokens_cached": int(getattr(
                    usage, "cached_input_tokens", 0) or 0),
                "tokens_new": int(getattr(
                    usage, "cache_written_tokens", 0) or 0),
                "at": datetime.now(timezone.utc).isoformat(),
            })
            if not isinstance(need, dict) or attempt >= self.LOOKS_PER_SUMMARY:
                break
            # It asked. Answer from the group, and put the question again.
            #
            # THE WHOLE EXCHANGE IS KEPT, because a summary that looked
            # things up is not one call and cannot be read as one: what it
            # asked for, what it got back and what was then put to it are
            # each a thing that can be wrong on its own. A panel showing
            # only the last prompt hides a search that returned nothing
            # and a summary written anyway.
            found, showed = self._look(group, need)
            # The ids it got back whole, so the page can say which states
            # the model has read every field of.
            group.setdefault("seen", {}).setdefault("in_full", []).extend(
                one for one in showed
                if one not in group["seen"]["in_full"])
            out["looks"].append({
                "number": len(out["looks"]) + 1,
                "asked_with": asked,
                "it_replied": reply,
                "query": {"keywords": list(need.get("keywords") or []),
                          "ids": list(need.get("ids") or [])},
                "found": found,
                "showed": list(showed),
                "found_nothing": ("0 match" in found
                                  or "asked for nothing" in found),
                "searched_within": group["size"],
            })
            # WHAT WAS ASKED, beside what came back. The follow-up said
            # only "you asked for this, and here it is", which leaves the
            # model to remember its own question across a turn and to
            # work out whether what arrived is an answer to it. Worse,
            # when a search matched nothing, the block was a heading with
            # nothing under it - and a model reading that cannot tell a
            # search that found nothing from a search that was never run.
            wanted = ", ".join(
                [f'the words "{w}"' for w in (need.get("keywords") or [])]
                + [f"the state {i}" for i in (need.get("ids") or [])])
            if not wanted:
                wanted = ("nothing - your request named no keywords "
                          "and no ids")
            asked = (asked + "\n\n"
                     + "YOU ASKED TO LOOK SOMETHING UP, AND HERE IS WHAT "
                       "CAME BACK.\n\n"
                     + f"What you asked for: {wanted}.\n"
                     + f"Where it looked: the {group['size']} states of "
                       f"this group, and nowhere else.\n"
                     + f"What it found: {_how_much(found)}.\n\n"
                     + found
                     + "\n\nNow answer with the JSON object. If this "
                       "still does not name the thing, you may ask once "
                       "more - but if the search came back empty, that is "
                       "itself the answer: the states really do not say, "
                       "and you can write that as a finding rather than a "
                       "guess.")
            out["looks"][-1]["then_asked"] = asked
            self._show_the_prompt(
                report, asked, group=group.get("id"),
                searches=len(out["looks"]),
                waiting_for=(f"the model's answer, after look "
                             f"{len(out['looks'])}"))
        if report is not None:
            stages = report.get("stages") or []
            if stages:
                stages[-1]["waiting_for"] = ""
        # Carried onto the summary so `_label` can hand it to the page
        # without the page having to find the community the task came from.
        out["seen"] = dict(group.get("seen") or {})
        out["searched"] = bool(out["looks"])
        out["searches"] = len(out["looks"])
        # Whether each search led to another. The page says so beside the
        # follow-up prompt, because "it searched twice" and "it searched,
        # was not satisfied, and searched again" are the same number and
        # different findings.
        for number, look in enumerate(out["looks"], start=1):
            look["went_on"] = number < len(out["looks"])
        out["prompt"] = asked
        out["prompt_tokens"] = estimate_tokens(asked)
        out["sent_tokens"] = (out["prompt_tokens"]
                              + estimate_tokens(instruction)
                              * max(1, int(out.get("calls", 1))))
        out["ms"] = int((time.perf_counter() - began) * 1000)
        usage = None
        del usage
        out["answer"] = getattr(response, "text", "") or ""
        out["written_by"] = _writer(response)
        answered = _as_json(out["answer"]) or {}
        # The next steps, read before anything can turn the answer away: a
        # call that gave no name still saw the user's changes, and what it
        # said about them is kept (see `_land_todo`).
        if group.get("todo") is not None:
            out["todo"] = _todo.read_answer(answered)
            out["todo_note"] = _without_ids(
                str(answered.get("todo_note") or "").strip())[:300]
        # WHAT WAS LEFT OUT IS KEPT - which is what the model was told.
        #
        # An answer to an ALREADY KNOWN prompt is an amendment: the model
        # sends what is now wrong and leaves the rest. Reading the name
        # and the summary from the answer alone treated every amendment
        # that did not happen to touch them as an answer with no name in
        # it, and threw the whole of it away - the new status, the new
        # weights and all. The description it amends supplies anything
        # it did not send.
        sent_english = dict(answered.get("in_english") or {}) \
            if isinstance(answered.get("in_english"), dict) else {}
        answered = _amended(answered, group.get("described"))
        title = str(answered.get("title") or "").strip()
        summary = str(answered.get("summary") or "").strip()
        if not title or not summary:
            out["note"] = ("The model returned no title or no summary, so "
                           "nothing was written. A group with a title and "
                           "nothing under it is a label.")
            # AND SAID WHERE SOMEBODY WILL SEE IT.
            #
            # This note has always been written onto `out`, where it is
            # visible if you go looking at the right panel at the right
            # moment - which is to say, in practice, nowhere. Three tasks
            # went unnamed for a day while the round, the gate, the flag
            # and the drawing were each examined in turn, because the one
            # fact that would have explained it - what the model actually
            # said - was never anywhere a reader passes.
            #
            # The answer itself goes in the log, cut short. Whether it
            # arrived empty, stopped half way, or came back whole and
            # unparsed are three different faults that look identical
            # from here, and the text tells them apart at a glance.
            answer = str(out.get("answer") or "")
            log.warning(
                "no name came back for %s: %d chars of answer, "
                "parsed into %d field(s)%s | answer begins: %.200r",
                str(group.get("id") or "a group"), len(answer),
                len(answered or {}),
                "" if answer.strip() else " | THE ANSWER WAS EMPTY",
                answer[:200])
            # The exchange is still on `out`, so the panel shows what it
            # actually said. This is the case that matters most for
            # that: no record is written, so the only other trace of the
            # call is a line saying it produced nothing.
            return out
        # AND CHECKED. The prompt forbids ids in what the user reads, and
        # a prompt is an instruction rather than a guarantee: the ids are
        # right there in what the model was shown, and a model asked to
        # cite its sources will cite what it can see. Anything that looks
        # like one is taken out and the fact is recorded, because a rule
        # that is silently broken is a rule nobody knows to fix.
        leaked = _ids_in(title) + _ids_in(summary)
        if leaked:
            out["leaked_ids"] = sorted(set(leaked))
            log.info("the summary for %s named %s state id(s); removed",
                     group.get("id"), len(out["leaked_ids"]))
        title = _without_ids(title)
        summary = _without_ids(summary)
        out["title"] = title
        out["summary"] = summary
        # WHETHER IT SUMMARISED OR LISTED. The prompt asks for one statement
        # at one altitude, and the failure it asks against is the one that
        # looks most like success: "fix the fetch step, re-run the scripts
        # and check the figures" says everything the states say and names
        # nothing they were FOR. It is counted rather than corrected - the
        # fix is to go up a level, which is not something that can be done
        # to a finished sentence - and shown on the debugger so a change to
        # the prompt can be judged by whether this number moves.
        # The worse of the two, not the two added up: a title reading
        # "the visa and expense paperwork" is one errand with two names and
        # so is its summary, and adding those together would flag a pair of
        # honest sentences as a list. What is being looked for is one
        # sentence carrying several things, so each is judged on its own.
        out["joins"] = max(_is_a_list(title), _is_a_list(summary))
        out["reads_as_a_list"] = out["joins"] >= 2
        out["reasoning"] = _without_ids(
            str(answered.get("reasoning") or "").strip())
        # Whether there is a thing underneath this, or only a way of
        # working. Missing counts as a thing: a model that did not answer
        # the question has not told us the group is a pattern, and hiding
        # a real task because a field was absent is the worse mistake.
        said = answered.get("concrete")
        out["concrete"] = True if said is None else bool(said)
        out["concrete_why"] = str(answered.get("concrete_why") or "").strip()
        # A TASK INSIDE A GROUP THAT IS NOT ONE, by the shown states it is
        # about. Only meant for a group turned down: a group that IS a task
        # has nothing to take out of it.
        if not out["concrete"]:
            buried = _buried_in(answered.get("buried"), group)
            if buried:
                out["buried"] = buried
        # NOT THIS WORK: shown states the model says are about something
        # else - taken out of the task by `_take_out_other_work`.
        shown = [str((one.get("member") or {}).get("id") or "")
                 for one in group.get("sample") or []]
        other = _numbered_in(answered.get("not_this_work"), shown)
        if other:
            out["not_this_work"] = other
        # The four parts, and WHICH of them this call actually sent. The
        # difference matters: a part that came back empty was left out on
        # purpose - it is kept as it was - and one that came back with
        # words in it is a new claim. Treating the two the same would let
        # a round that only had something to say about `status` erase the
        # other three.
        out["described"] = {}
        out["described_now"] = []
        for part in DESCRIBED_IN:
            said = _without_ids(str(answered.get(part) or "").strip())
            if not said:
                continue
            out["described"][part] = said[:600]
            out["described_now"].append(part)
        # ITS ENGLISH COPY, where the user does not read English: what it is
        # measured and compared by.
        english = answered.get("in_english")
        if isinstance(english, dict):
            kept = {part: _without_ids(str(value).strip())[:600]
                    for part, value in english.items()
                    if part in ("title", "summary") + tuple(DESCRIBED_IN)
                    and str(value or "").strip()}
            if kept:
                # Each part sent now marked as a copy of the words written
                # now; the rest keep their marks (english_copies.py).
                from .english_copies import stamped
                kept["_of"] = dict(english.get("_of") or {})
                out["described"]["in_english"] = stamped(
                    kept, dict(answered, **out["described"]), sent_english.keys())
        out["changed_why"] = str(answered.get("changed_why") or "").strip()
        # HOW MUCH IT MATTERS AND HOW SOON, on the same 1-7 scale the
        # traits use. Asked rather than inferred: the agent sees files
        # and pages, and a number worked out from how many states a task
        # holds would be a measure of time spent wearing the name of
        # importance. Out of range or missing is 4 - the middle - which
        # is what the instruction says to answer when the states do not
        # say, so a model that skipped the question and one that judged
        # it undecidable land in the same place.
        for name in ("urgency", "importance"):
            try:
                mark = int(answered.get(name))
            except (TypeError, ValueError):
                mark = 0
            out[name] = mark if 1 <= mark <= 7 else 4
            out[name + "_given"] = 1 <= mark <= 7
        out["weighed_why"] = str(answered.get("weighed_why") or "").strip()
        out["evidence"] = [
            piece for piece in
            (_without_ids(str(item))[:300]
             for item in (answered.get("evidence") or []))
            # An item that was nothing but an id is dropped rather than
            # left as an empty bullet.
            if piece.strip(" ,;-")][:12]
        # Before the record, so the record can point at it.
        record = self._write(group, out, trace_id, where)
        if record is not None:
            out["record_id"] = record.id
            out["path"] = record.path
        return out

    #: How many states one search hands back, and how many searches a
    #: summary may make.
    #:
    #: Twelve because a search that returns forty has returned the group
    #: again; twice because a third look has never been the one that found
    #: the answer, and every look is a model call.
    FOUND_PER_LOOK = 12
    #: How many plans' answers are kept to be read again (see `_plan`):
    #: the lot, and the pieces of each task, with room to spare.
    PLANS_KEPT = 64
    LOOKS_PER_SUMMARY = 2

    @staticmethod
    def _add_usage(out: Dict[str, Any], usage: Any) -> None:
        """Add one call's tokens to what this summary has cost so far.

        Summaries take more than one call now, so these accumulate. Read
        from four fields rather than one: `input_tokens` is only what was
        neither cached nor caching, which on a cached provider is a
        handful and reads as though the call were free.
        """
        out["tokens_in"] = (int(out.get("tokens_in", 0))
                            + int(getattr(usage, "input_tokens", 0) or 0))
        out["tokens_out"] = (int(out.get("tokens_out", 0))
                             + int(getattr(usage, "output_tokens", 0) or 0))
        out["tokens_cached"] = (
            int(out.get("tokens_cached", 0))
            + int(getattr(usage, "cached_input_tokens", 0) or 0))
        out["tokens_new"] = (
            int(out.get("tokens_new", 0))
            + int(getattr(usage, "cache_written_tokens", 0) or 0))

    def _bring_status_up_to_date(self, report: Dict[str, Any], task: Any,
                                 index: int, nodes: Sequence[_Node],
                                 trace_id: str, where: _Where,
                                 summary: Dict[str, Any]) -> None:
        """Ask where a task stands, and keep what comes back.

        Called for a task whose summary was not rewritten this round. A
        task that WAS rewritten has just had all four parts answered from
        every state it holds, so asking again would be a second call to
        confirm a sentence written a moment ago.

        In three parts, so a round can run the middle one - the call -
        beside the others (see `_status_to_ask`); this is the three in a
        row.
        """
        asking = self._status_to_ask(report, task, index, nodes, where)
        if asking is None:
            return
        said = self._status_asked(report, task, asking)
        self._keep_status(report, task, summary, said, where)

    def _status_to_ask(self, report: Dict[str, Any], task: Any,
                       index: int, nodes: Sequence[_Node],
                       where: _Where) -> Optional[Dict[str, Any]]:
        """What to ask about where a task stands, or None if nothing.

        BUILT HERE, ASKED ELSEWHERE. The status calls of a round were made
        one after another inside the loop that hands summaries out, so
        each of them held up every summary behind it and none ran beside
        another: on the live folder a round of kept summaries was a minute
        of calls in a row with room for two at a time. The question is
        still put together here, at the point in the loop where it always
        was and from exactly what it always read - this task's own
        description and the states that moved in it, nothing another
        task's call writes - so the text sent is the same; only the call
        is handed to `at_once`, and what comes back is kept on this thread
        by `_keep_status`, as before.
        """
        moved = (report.get("moved") or {}).get(task.id) or {}
        held_now = dict(getattr(task, "described", {}) or {})
        # A task carried over from before the weighing existed has no
        # urgency and no importance, and nothing would ever give it any:
        # the full summary is what weighs a task, and a task whose states
        # sit still is never summarised again. It would sort below
        # everything for good. So a missing weight is itself worth the
        # call, once.
        unweighed = (held_now.get("urgency") is None
                     or held_now.get("importance") is None)
        # AND A TASK AT THE TOP WHOSE NEXT STEPS THE USER CHANGED. What
        # they changed is told to the next call about it, and with nothing
        # else moving there would be no next call - the edit would sit
        # there unread until some unrelated work happened in the task.
        top = not self._in_a_project(getattr(task, "id", "") or "")
        todo_due = top and self._todo_due("state_tasks", where.scope,
                                          getattr(task, "id", "") or "")
        if (not moved.get("gained") and not moved.get("lost")
                and not unweighed and not todo_due):
            # LOOKED AT, AND NOTHING TO SAY - which is up to date. Not
            # recorded, a task the round planned to bring up to date and
            # found unchanged stayed "not done" for good, and a finished
            # round's bar stopped at 85%.
            if where.scope != SANDBOX:
                self._also_status(getattr(task, "id", "") or "")
            return None
        entry = ((report.get("layers") or [{}])[index]
                 if index < len(report.get("layers") or []) else {})
        # ONE MORE THING THAT IS WORK ON A TASK. A round with many kept
        # summaries spends most of its time here, and nothing was pulsing
        # through any of it - so a reader watching saw a still picture and
        # a round that was plainly busy.
        report["summarising"] = entry.get("id") or task.id
        if (report.get("plan") or {}).get("shape_only"):
            report["plan"] = self._the_shape_so_far(report, nodes)
        asking = self._status_question(
            report, task, entry, nodes, moved,
            todo_scope=where.scope if top else None, todo_due=todo_due)
        return asking if asking.get("asked") else None

    def _where_it_stands(self, report: Dict[str, Any], task: Any,
                         entry: Dict[str, Any], nodes: Sequence[_Node],
                         moved: Dict[str, List[str]], trace_id: str,
                         where: _Where) -> Dict[str, Any]:
        """The status question built and put, in a row - or why not."""
        asking = self._status_question(report, task, entry, nodes, moved)
        if not asking.get("asked"):
            return asking
        return self._status_asked(report, task, asking)

    def _keep_status(self, report: Dict[str, Any], task: Any,
                     summary: Dict[str, Any], said: Dict[str, Any],
                     where: _Where) -> None:
        """Keep what a status call said, on the task and on its summary."""
        # ITS NEXT STEPS FIRST, because they are kept even when the call
        # never got through - kept whole then, since nothing saw them.
        if said.get("todo_shown") is not None:
            self._land_todo("state_tasks",
                            said.get("todo_scope") or where.scope, task,
                            said["todo_shown"], said.get("todo"),
                            str(said.get("todo_note") or ""),
                            failed=not said.get("calls"))
            if isinstance(summary.get("described"), dict):
                summary["described"].update(_todo.drawn(task.described))
        if not said.get("calls"):
            return
        self._spent(report, said)
        summary["status_call"] = said
        if said.get("not_this_work") and getattr(task, "profiled", False):
            self._take_out_other_work(task, said["not_this_work"], where, report, "status")
        if where.scope != SANDBOX:
            self._also_status(task.id)
        report.setdefault("status_calls", []).append(
            {"task": task.id, "changed": bool(said.get("changed")),
             "why": said.get("why", ""), "arrived": said.get("arrived", 0),
             "left": said.get("left", 0), "ms": said.get("ms", 0),
             "tokens_in": said.get("tokens_in", 0),
             "tokens_out": said.get("tokens_out", 0)})
        held = dict(getattr(task, "described", {}) or {})
        written: List[str] = []
        for part in ("urgency", "importance"):
            if said.get(part) is not None:
                held[part] = said[part]
                written.append(part)
        if said.get("weighed"):
            held["weighed_why"] = said["weighed"]
        if said.get("urgency") is not None:
            held["weighed_how"] = "read"
        if said.get("changed") and said.get("status"):
            held["status"] = said["status"]
            written.append("status")
            if said.get("status_in_english"):
                from .english_copies import stamped
                held["in_english"] = stamped(
                    dict(held.get("in_english") or {}, status=said["status_in_english"]),
                    held, ["status"])
        if not written:
            return
        task.described = held
        summary["described"] = held
        summary["described_now"] = written
        # The panel SORTS on the summary's own copy and READS the card
        # from `described`. Updating one and not the other would have the
        # tree ordered by last round's urgency while the card beside it
        # showed this round's - the same fault that made a weighed task
        # look unweighed, one level along.
        for name in ("urgency", "importance"):
            if held.get(name) is not None:
                summary[name] = int(held[name])
        if held.get("weighed_why"):
            summary["weighed_why"] = held["weighed_why"]
        summary["changed_why"] = said.get("why", "")
        try:
            self._kept_now.save([task])
        except Exception:            # pragma: no cover - a save, not a step
            log.debug("could not keep the new status for %s", task.id,
                      exc_info=True)

    def _status_question(self, report: Dict[str, Any], task: Any,
                         entry: Dict[str, Any], nodes: Sequence[_Node],
                         moved: Dict[str, List[str]],
                         todo_scope: Optional[str] = None,
                         todo_due: bool = False) -> Dict[str, Any]:
        """The question that answers `status` alone, from the states that
        moved - or, when there is nothing to ask, the empty account saying
        why. `_status_asked` puts it.

        The full summary is written only when a third of a task has
        turned over, which is right for the other three parts - where the
        work lives, what it is in aid of and what it consists of do not
        change because four states arrived - and wrong for this one.
        `status` is the part whose whole job is to move, and under that
        rule it sat still for hours at a time while the task it described
        went on happening.

        So it is asked every round in which the membership changed at
        all. The call is small: the description it already has, and the
        states that arrived or left - not the forty it holds. It is told
        it may answer that nothing changed, because a status rewritten
        in new words every hour is the thing the turnover rule was
        protecting the reader from, and reintroducing it here would be
        trading one fault for the other.
        """
        out: Dict[str, Any] = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        if self.models is None:
            return out
        held = dict(getattr(task, "described", {}) or {})
        if not held.get("status"):
            # Nothing to update: this task has never been described, so
            # the full summary will write all four parts or none.
            out["skipped"] = "it has no description to bring up to date"
            return out
        seats = {node.id: node for node in nodes}
        arrived = [seats[one] for one in (moved.get("gained") or [])
                   if one in seats]
        left = [seats[one] for one in (moved.get("lost") or [])
                if one in seats]
        if not arrived and not left and not todo_due:
            out["skipped"] = "its states did not change this round"
            return out

        lines = [f"THE TASK, AS IT IS DESCRIBED NOW",
                 f"title: {(entry.get('summary') or {}).get('title') or '?'}",
                 ""]
        for part in DESCRIBED_IN:
            lines.append(f"{part}: {held.get(part) or '(not said)'}")
        lines += ["", f"IT HAS TAKEN {getattr(task, 'count', 0) or len(getattr(task, 'members', []) or [])} "
                      f"STATES IN ALL.", ""]
        idle = (getattr(task, "happened", None) or {}).get("dormant_days")
        if (getattr(task, "happened", None) or {}).get("kind") == "picked up again":
            lines += [f"IT HAD BEEN DORMANT"
                      + (f" FOR {idle} DAYS" if idle is not None else "")
                      + " AND HAS JUST BEEN PICKED UP AGAIN - say so in the "
                        "status if the new states bear it out.", ""]
        if arrived:
            lines.append(f"{len(arrived)} STATE(S) ARRIVED IN IT:")
            lines.append("")
            for number, node in enumerate(arrived[:STATES_PER_STATUS], start=1):
                lines.extend(_whole_state(node.to_dict(),
                                          f"arrived {number}"))
            if len(arrived) > STATES_PER_STATUS:
                lines.append(f"({len(arrived) - STATES_PER_STATUS} more "
                             f"arrived and are not shown.)")
                lines.append("")
        if left:
            lines.append(f"{len(left)} STATE(S) ARE NO LONGER IN IT - the "
                         f"grouping decided they belong elsewhere:")
            lines.append("")
            for number, node in enumerate(left[:STATES_PER_STATUS], start=1):
                lines.extend(_whole_state(node.to_dict(), f"left {number}"))
            if len(left) > STATES_PER_STATUS:
                lines.append(f"({len(left) - STATES_PER_STATUS} more left "
                             f"and are not shown.)")
        if not arrived and not left:
            lines += ["NO STATE ARRIVED OR LEFT. It is asked because the "
                      "user changed its next steps: answer `changed` false "
                      "unless the status really is wrong, and give the new "
                      "list.", ""]
        # ITS NEXT STEPS, when it is at the top: shown on every status
        # call, so the list moves with the work as well as with the user.
        shown = (self._todo_asking("state_tasks", todo_scope, task.id)
                 if todo_scope else None)
        if shown is not None:
            lines += ["", _todo.told(shown)]
        asked = "\n".join(lines)
        instruction = STATUS_PROMPT.replace(
            "{status_in_english}", STATUS_ENGLISH if needs_english_copy(self.config) else "")
        if shown is not None:
            instruction = instruction + "\n\n" + _todo.rules("task")
        self._at(report, "bringing a status up to date",
                 f"{entry.get('id') or task.id}, "
                 f"{len(arrived)} in, {len(left)} out")
        self._show_the_prompt(report, asked, instruction=instruction,
                              waiting_for="where it stands now")
        return {"asked": asked, "instruction": instruction,
                "arrived": len(arrived), "left": len(left), "todo": shown,
                "todo_scope": todo_scope,
                # By their numbers, for "not_this_work".
                "arrived_ids": [node.id for node in arrived[:STATES_PER_STATUS]]}

    def _status_asked(self, report: Dict[str, Any], task: Any,
                      asking: Dict[str, Any]) -> Dict[str, Any]:
        """Put the question `_status_question` built, and read the answer.

        Touches nothing but what it returns, so it can run beside the
        round's other calls.
        """
        out: Dict[str, Any] = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        asked, instruction = asking["asked"], asking["instruction"]
        # What it was shown of the next steps, carried to `_keep_status` -
        # which clears exactly that once an answer has come back.
        if asking.get("todo") is not None:
            out["todo_shown"] = asking["todo"]
            out["todo_scope"] = asking.get("todo_scope")
        began = time.perf_counter()
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": asked}],
                             system=instruction,
                             language=STATUS_LANGUAGE,
                             # Room for the list as well as the status.
                             max_tokens=(1100 if asking.get("todo") is not None
                                         else 700),
                             task="state_heartbeat",
                             metadata={"task": task.id, "kind": "status"}),
                role="heartbeat")
        except ModelUnavailable as exc:
            out["blocked"] = str(exc)
            return out
        out["calls"] = 1
        self._add_usage(out, getattr(response, "usage", None))
        reply = getattr(response, "text", "") or ""
        answered = _as_json(reply) or {}
        out["ms"] = int((time.perf_counter() - began) * 1000)
        out["prompt"] = asked
        out["answer"] = reply
        out["exchange"] = [{"call": 1, "instruction": instruction,
                            "sent": asked, "reply": reply,
                            "read_as": answered, "was_json": bool(answered),
                            "asked_to_search": False,
                            "what_it_asked_for": None,
                            "at": datetime.now(timezone.utc).isoformat()}]
        out["why_no_search"] = ("This call was not offered a search: it is "
                                "shown one task's description and the states "
                                "that moved, and asked only where it stands.")
        said = _without_ids(str(answered.get("status") or "").strip())
        out["changed"] = bool(answered.get("changed")) and bool(said)
        out["why"] = str(answered.get("why") or "").strip()
        out["arrived"] = asking["arrived"]
        out["left"] = asking["left"]
        for part in ("urgency", "importance"):
            out[part] = _a_number_between(answered.get(part), 1, 7)
        out["weighed"] = str(answered.get("weighed") or "").strip()[:300]
        other = _numbered_in(answered.get("not_this_work"), asking.get("arrived_ids") or [])
        if other:
            out["not_this_work"] = other
        if out["changed"]:
            out["status"] = said[:600]
            english = _without_ids(str(answered.get("status_in_english") or "").strip())
            if english:
                out["status_in_english"] = english[:600]
        if asking.get("todo") is not None:
            out["todo"] = _todo.read_answer(answered)
            out["todo_note"] = _without_ids(
                str(answered.get("todo_note") or "").strip())[:300]
        return out

    def _look(self, group: Dict[str, Any],
              need: Dict[str, Any]) -> Tuple[str, List[str]]:
        """What the group holds on the thing that was asked for.

        Scoped to this group and nothing else. A search that reached the
        whole calendar would answer the question with a state from some
        other month, and the model has no way to tell that it had - it
        would name the wrong paper with perfect confidence.
        """
        members = list(group.get("members") or [])
        held = {one["id"]: one for one in members}
        lines: List[str] = []
        showed: List[str] = []

        wanted = [str(one) for one in (need.get("ids") or [])][:self.FOUND_PER_LOOK]
        missing = [one for one in wanted if one not in held]
        for state_id in wanted:
            member = held.get(state_id)
            if member is None:
                continue
            lines.extend(_whole_state(member, f"{state_id}, in full"))
            showed.append(str(state_id))
        if missing:
            lines.append(f"(No state in this group has the id "
                         f"{', '.join(missing)}.)")
            lines.append("")

        words = [str(one).strip().lower()
                 for one in (need.get("keywords") or []) if str(one).strip()]
        if words:
            scored = []
            for member in members:
                haystack = " ".join([
                    str(member.get("title") or ""),
                    str(member.get("detail") or ""),
                    str(member.get("reasoning") or ""),
                    " ".join(member.get("sources") or []),
                    " ".join(member.get("evidence") or []),
                ]).lower()
                hits = sum(1 for word in words if word in haystack)
                if hits:
                    scored.append((hits, member))
            scored.sort(key=lambda row: -row[0])
            lines.append(f"Searched all {len(members)} states in this group "
                         f"for {', '.join(words)}: {len(scored)} match.")
            if not scored:
                lines.append("Nothing in this group says any of those words.")
            for hits, member in scored[:self.FOUND_PER_LOOK]:
                lines.append("")
                # The same renderer as a lookup by id. Two of them was the
                # first version and they had already drifted: the one here
                # left out when the state ran and capped its evidence at
                # twelve, so the answer depended on how it was asked for.
                lines.extend(_whole_state(
                    member, f"{member['id']}, {hits} of those words"))
                showed.append(str(member["id"]))
            if len(scored) > self.FOUND_PER_LOOK:
                lines.append("")
                lines.append(f"({len(scored) - self.FOUND_PER_LOOK} more match "
                             f"and are not shown. Ask for one by id if you "
                             f"need it.)")
        if not lines:
            lines.append("That asked for nothing: no ids and no keywords.")
        return "\n".join(lines), showed

    def _sample_for(self, group: Dict[str, Any],
                    vectors: Dict[str, List[float]],
                    where: _Where) -> List[Dict[str, Any]]:
        """This group's sample: the one this round chose, or a new one.

        Kept beside the round's grouping, because it belongs to the same
        decision and goes stale at the same moment - when the round ends.
        """
        which = str(group.get("id") or "")
        held = self._samples_kept()
        if which and which in held:
            # The members themselves, found again by id in what this
            # round is holding, so the saved part is only the choice.
            by_id = {str(one.get("id") or ""): one
                     for one in (group.get("members") or [])}
            picked = [dict(row, member=by_id[row["member_id"]])
                      for row in held[which]
                      if row.get("member_id") in by_id]
            if picked:
                return picked
        chosen = self._sample(group, vectors or {})
        if which and where.scope != SANDBOX:
            self._keep_a_sample(which, chosen)
        return chosen

    def _samples_kept(self) -> Dict[str, List[Dict[str, Any]]]:
        """The samples this round has already chosen."""
        try:
            held = json.loads(
                self._grouping_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if str(held.get("round") or "") != str(
                self.config.get("state_round_started_at", "")):
            return {}
        out = held.get("samples")
        return out if isinstance(out, dict) else {}

    def _keep_a_sample(self, which: str,
                       chosen: Sequence[Dict[str, Any]]) -> None:
        """Write one group's choice down beside the grouping."""
        try:
            path = self._grouping_file()
            held = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if str(held.get("round") or "") != str(
                self.config.get("state_round_started_at", "")):
            return
        samples = held.get("samples")
        if not isinstance(samples, dict):
            samples = {}
        samples[which] = [
            {"member_id": str((one.get("member") or {}).get("id") or ""),
             "weight": one.get("weight", 1),
             "stands_for": one.get("stands_for", 1)}
            for one in chosen
            if (one.get("member") or {}).get("id")]
        held["samples"] = samples
        try:
            path.write_text(json.dumps(held), encoding="utf-8")
        except OSError:          # pragma: no cover - a note, not the work
            log.debug("could not keep the sample for %s", which)

    def _sample(self, group: Dict[str, Any],
                vectors: Dict[str, List[float]],
                budget: int = STATES_PER_SUMMARY) -> List[Dict[str, Any]]:
        """Which states to show the model, and what each stands for.

        THE OLD ANSWER WAS THE FIRST FORTY, and it was worse than a
        truncation. The members are in the order the tree walks them, so
        the first forty of three hundred are one contiguous branch - one
        sub-subject - and the model read a corner and described it as the
        whole. Worse, the prompt asks it to say where the edges are, from
        a sample chosen so as to exclude them.

        So: the group's own tree is cut into `budget` pieces at its
        tallest joins, and one state is taken from each - the one nearest
        that piece's middle, which is the most typical thing in that
        region rather than an arbitrary member. Each carries the number it
        stands for, and that number is the part that fixes accuracy:
        without it the model can only guess proportions, and with it "most
        of this is X, and two of them are Y" is a claim it can get right.

        Three states are forced in, displacing the lightest picks: the
        earliest, the latest, and the one farthest from the middle of the
        whole group. The first two make drift visible - a project walking
        from the code to the writing-up - and the third is the best
        candidate for the thing that does not belong.

        No quotas beyond those three. Reserving slots for "the centre" and
        "the edges" was the first design and it would make every summary
        sound like it was mostly oddities: the tree cut already gives
        proportional coverage, which is what proportional means.
        """
        members = list(group.get("members") or [])
        if len(members) <= budget:
            return [{"member": one, "weight": 1, "stands_for": 1}
                    for one in members]

        order = [one["id"] for one in members]
        held = {one["id"]: one for one in members}
        known = [one for one in order if one in vectors]
        if len(known) <= budget or self.embedder is None:
            return [{"member": held[one], "weight": 1, "stands_for": 1}
                    for one in order[:budget]]

        pairs = self.embedder.pairwise(known, vectors)
        distance = {(i, j): 1.0 - float(score) for i, j, score in pairs}
        merges = cluster(len(known), distance)
        pieces = _cut_into(len(known), merges, budget)

        middle_of_all = _unit_vector([sum(values) for values in
                                      zip(*[vectors[one] for one in known])])

        def apart_from(vector, other) -> float:
            return 1.0 - sum(a * b for a, b in zip(vector, other))

        chosen: List[Dict[str, Any]] = []
        for piece in pieces:
            rows = [vectors[known[seat]] for seat in piece]
            middle = _unit_vector([sum(values) for values in zip(*rows)])
            best = min(piece, key=lambda seat: apart_from(vectors[known[seat]],
                                                         middle))
            chosen.append({"member": held[known[best]], "weight": len(piece),
                           "stands_for": len(piece)})

        # The three that have to be there, whatever the cut said.
        def when(one: Dict[str, Any]) -> str:
            return str(one["member"].get("starts_at") or "")

        must: List[str] = []
        by_time = sorted((one for one in members if one["id"] in vectors),
                         key=lambda one: str(one.get("starts_at") or ""))
        if by_time:
            must.extend([by_time[0]["id"], by_time[-1]["id"]])
        farthest = max(known, key=lambda one: apart_from(vectors[one],
                                                         middle_of_all))
        must.append(farthest)

        have = {one["member"]["id"] for one in chosen}
        for wanted in must:
            if wanted in have or wanted not in held:
                continue
            # Displace the lightest pick that is not itself a must.
            spare = [one for one in chosen
                     if one["member"]["id"] not in must]
            if not spare:
                break
            lightest = min(spare, key=lambda one: one["weight"])
            chosen.remove(lightest)
            chosen.append({"member": held[wanted], "weight": 1,
                           "stands_for": 1, "because": "an edge of the group"})
            have.add(wanted)

        # In time order, so that a group which walked somewhere reads as
        # having walked rather than as a jumble.
        chosen.sort(key=when)
        return chosen

    def _prompt(self, group: Dict[str, Any]) -> str:
        """The group as the model is shown it.

        Each state in full - what it said, what it rested on, when it ran
        and when it was written - because the question is what they have in
        common, and a list of titles is a list of one sentence each with
        the evidence left out.
        """
        lines = [f"{group['size']} states, grouped because of what they mean",
                 f"- each was read as a vector and the closest were joined, "
                 f"so two of them can be here without sharing a word.",
                 f"They run from {group.get('from') or 'an unrecorded start'} "
                 f"to {group.get('to') or 'an unrecorded end'}.",
                 "",
                 "Each `ran:` line below is the USER'S OWN CLOCK - their "
                 "local time, the day of the week it was, and the zone in "
                 "square brackets. That is the week they actually had: a "
                 "state is a Tuesday evening or a Sunday afternoon, and "
                 "those are different facts about a person. Where a line "
                 "says UTC instead, that state has no local time recorded "
                 "and you cannot tell which day of theirs it fell on.", ""]
        if group["shared_said"]:
            lines.append("Words every one of them uses: "
                         + ", ".join(group["shared_said"]))
        if group["shared_sources"]:
            lines.append("Files, pages or windows every one of them rests on:")
            lines += [f"  {item}" for item in group["shared_sources"]]
        lines.append("")
        sample = group.get("sample") or [
            {"member": one, "weight": 1, "stands_for": 1}
            for one in group["members"][:STATES_PER_SUMMARY]]
        for number, picked in enumerate(sample, start=1):
            member = picked["member"]
            # Numbered, not named. The id is a random hex string: it tells
            # the model nothing, it cannot be looked up from there, and it
            # is twenty tokens per state spent on being unable to confuse
            # two of them - which a number does for one.
            stands = int(picked.get("stands_for", 1) or 1)
            # The id is back. It says nothing on its own - it is a random
            # string - but it is how a state is asked for by name, and
            # being able to ask is worth the tokens.
            lines.append(f"--- {number} [{member['id']}] ---" if stands <= 1
                         else f"--- {number} [{member['id']}], and "
                              f"{stands - 1} more like it ---")
            lines.append(f"title: {member['title']}")
            if member["detail"]:
                lines.append(f"detail: {member['detail']}")
            # WHEN IT RAN, and not when it was written down. The second is
            # a fact about this application's own scheduling - the review
            # noticing something a quarter of an hour later - and the
            # question here is what the user was doing.
            lines.append(_when_it_ran(member))
            for item in member["sources"][:8]:
                lines.append(f"  rests on: {item}")
            for item in member["evidence"][:8]:
                lines.append(f"  evidence: {item}")
            lines.append("")
        left_out = group["size"] - sum(int(one.get("stands_for", 1) or 1)
                                       for one in sample)
        if len(sample) < group["size"]:
            lines.append(
                f"({len(sample)} of these {group['size']} states are shown, "
                f"not the first {len(sample)} but one from each of "
                f"{len(sample)} clusters of the group - so they cover all of "
                f"it - and the number beside each says how many near-identical "
                f"states it stands for. They are in time order. Weigh them by "
                f"those numbers: a state standing for forty others is most of "
                f"this group, and one standing for itself may be the member "
                f"that does not belong."
                + (f" {left_out} could not be placed and are not shown."
                   if left_out > 0 else ""))
        return "\n".join(lines)

    def _write(self, group: Dict[str, Any], summary: Dict[str, Any],
               trace_id: str, where: _Where) -> Optional[Record]:
        """The summary, as a record one level above the states it covers.

        In its own folder, beside the states rather than among them: a
        task and a state are different claims - the states can each be
        right while the grouping that joined them is wrong - and a reader
        holding one could not tell which they had. `covers` names the
        states, so the claim can still be checked against them.

        Which library it goes to is the caller's, so a sandbox round
        writes into the sandbox and touches nothing real.
        """
        if where.library is None:
            return None
        members = [member["id"] for member in group["members"]]
        body = [summary["summary"], ""]
        # The four parts, under their own headings: this is the part of the
        # record a person reads when they come back to something, and a
        # paragraph that answers all four at once answers none of them well.
        described = dict(summary.get("described") or {})
        if described:
            for part, heading in (("where", "Where the work lives"),
                                  ("goal", "What it is in aid of"),
                                  ("doing", "What the work is"),
                                  ("status", "Where it stands")):
                said = str(described.get(part) or "").strip()
                if said:
                    body += [f"{heading}:", said, ""]
        if summary.get("reasoning"):
            body += ["Why these belong together:", summary["reasoning"], ""]
        if summary.get("evidence"):
            body += ["What it rests on:"]
            body += [f"- {item}" for item in summary["evidence"]]
            body.append("")
        body += [f"Covers {group['size']} states, "
                 f"{group.get('from') or '?'} to {group.get('to') or '?'}:"]
        body += [f"- [{member['id']}] {member['title']}"
                 for member in group["members"]]
        try:
            return where.library.write(Record(
                # A task is not a state. They were the same folder, with
                # the summary distinguished only by sitting a level above
                # what it covered - so a reader holding one could not tell
                # which they had, and a search could not ask for either.
                folder=STATE_TASKS,
                title=summary["title"][:200],
                summary=summary["summary"][:500],
                body="\n".join(body),
                keywords=([word for word in group["shared_said"][:8] if word]
                          + ([] if summary.get("concrete", True)
                             else ["way-of-working", "not-a-task"])),
                references=[f"state heartbeat: {trace_id}"]
                           + [f"state: {member}" for member in members[:40]],
                source="state_heartbeat",
                # WHETHER THIS IS THE USER'S TO SEE. A group can be true
                # and still not be a task: "they keep coming back to
                # things in short sittings" is a way of working, and a
                # list of what somebody is working on with that in it is a
                # list with a horoscope in it. It is kept, because it is
                # something the agent knows, and marked so that nothing
                # puts it in front of the user as a task.
                metadata={"concrete": bool(summary.get("concrete", True)),
                          "concrete_why": summary.get("concrete_why", ""),
                          "shown_to_user": bool(summary.get("concrete", True)),
                          "described": dict(summary.get("described") or {}),
                          "searched": bool(summary.get("searched")),
                          "searches": int(summary.get("searches", 0) or 0),
                          "written_by": _writer_said(summary.get("written_by"))},
                # Section 50.2: one level above what it summarises, naming
                # what it summarises. A summary that does not say what it
                # covers cannot be checked and cannot be superseded.
                level=1,
                covers=members,
                # Section 50.4: which of the states underneath carries which
                # word. It is what makes a summary an index rather than a
                # label - going down from it is the same operation as
                # searching at the top, instead of reading forty titles and
                # guessing.
                child_index=self._child_index(group),
            ))
        except Exception:                # pragma: no cover - a write that failed
            log.exception("could not write the group summary")
            return None

    @staticmethod
    def _child_index(group: Dict[str, Any]) -> Dict[str, List[str]]:
        """Term to the states under this summary that carry it.

        Built from the terms already taken for the grouping, so it costs
        nothing: the words were read to decide these belong together, and
        this is the same reading kept rather than thrown away.
        """
        index: Dict[str, List[str]] = {}
        for member in group["members"]:
            for term in set(member["said_terms"]) | set(member["rests_on_terms"]):
                ids = index.setdefault(term, [])
                if member["id"] not in ids:
                    ids.append(member["id"])
        return index

    # -- the settings, read where they are used -----------------------------
    def _stands_out(self) -> float:
        return float(self.config.get("state_task_stands_out", STANDS_OUT))

    def _least_work(self) -> float:
        """How many seconds of work a task must hold at least."""
        return float(self.config.get("state_task_min_hours", 2.0) or 2.0) * 3600

    def _windows_of(self, state_ids: Sequence[str]) -> Dict[str, Tuple[str, float]]:
        """How long each state lasted, keyed by what it is counted under.

        HOW LONG IT SAYS IT LASTED. A state is written with the minutes it
        took - nine of a fifteen-minute window, four, two - and that is its
        work. Counting the whole window instead made a two-minute look at
        a changelog fifteen minutes of the task it was grouped into, so a
        task reached two hours on a fraction of that. Capped at the window
        it was read from, which it cannot have outlasted. Each such state
        is counted on its own: two things done in one window are two
        stretches of work.

        A state that records no minutes (written before it was asked for)
        counts the window it was written in, at that window's own length,
        once however many states came from it - and one that records no
        window either (a scheduled task's) counts the interval set now.
        """
        cache = self.__dict__.setdefault("_window_cache", {})
        wanted = [one for one in dict.fromkeys(state_ids) if one not in cache]
        if wanted:
            every = float(max(60, int(self.config.get(
                "heartbeat_interval_seconds", 900) or 900)))
            found = {}
            for where in {self.where(LIVE).scope: self.where(LIVE),
                          SANDBOX: self.where(SANDBOX)}.values():
                rest = [one for one in wanted if one not in found]
                if not rest:
                    break        # all found already: the sandbox is not read
                try:
                    for state in where.states.by_ids(rest):
                        found[str(getattr(state, "id", ""))] = state
                except Exception:      # pragma: no cover - a read
                    continue
            for one in wanted:
                meta = dict(getattr(found.get(one), "metadata", None) or {})
                looked = meta.get("looked_at") or {}
                minutes = looked.get("minutes") if isinstance(looked, dict) else None
                try:
                    seconds = float(minutes) * 60 if minutes else 0.0
                except (TypeError, ValueError):
                    seconds = 0.0
                try:
                    lasted = float(meta.get("minutes") or 0) * 60
                except (TypeError, ValueError):
                    lasted = 0.0
                if lasted > 0:
                    cache[one] = (f"lasted:{one}",
                                  min(lasted, seconds) if seconds > 0 else lasted)
                elif seconds > 0:
                    cache[one] = (f"window:{looked.get('from') or one}", seconds)
                else:
                    cache[one] = (f"state:{one}", every)
        return {one: cache[one] for one in state_ids if one in cache}

    def _work_in(self, state_ids: Sequence[str]) -> float:
        """Seconds of work these states add up to: how long each one says
        it lasted (`_windows_of`)."""
        windows = dict(self._windows_of(list(state_ids)).values())
        return sum(windows.values())

    def _projects_scheme(self) -> bool:
        """Whether runs write tasks and group them into projects (section
        34.5) - on unless switched off."""
        return bool(self.config.get("state_projects_enabled", True))

    def _too_short(self, task: Any) -> bool:
        """Less work in it than a task holds, so it is not one - as much as
        that, or more, is. A project of the per-run scheme is measured by
        the minutes its tasks were worked on."""
        profile = getattr(task, "profile", None) or {}
        if profile.get("scheme") == "projects":
            return float(profile.get("minutes") or 0.0) * 60 < self._least_work()
        return self._work_in(list(getattr(task, "members", None) or [])) \
            < self._least_work()

    def _min_nodes(self) -> int:
        return max(2, int(self.config.get("state_community_min_nodes", 5)))

    def _summaries_wanted(self, where: Optional[_Where] = None) -> bool:
        """Whether this round writes the summaries as well as finding them.

        Off for the real round while the tree is what is being read: the
        grouping needs no model at all, and a call per group is a minute
        of waiting for text nobody is looking at yet.

        ON in the sandbox, because a summary is the thing a person opening
        the sandbox came to look at - they made the states up in order to
        see what would be written about them. It is a deliberate press of
        a button rather than a timer, so the calls are asked for.
        """
        if where is not None and where.scope == SANDBOX:
            return bool(self.config.get("state_sandbox_summaries", True))
        return bool(self.config.get("state_summaries_enabled", False))

    # -- what the panel reads -----------------------------------------------
    @property
    def last_report(self) -> Dict[str, Any]:
        return dict(self._last_report)

    def _at(self, report: Dict[str, Any], stage: str,
            detail: str = "") -> None:
        """Say where the round has got to, in the report it is filling.

        `self._progress` and `report` are the same object, so a reader
        asking what is happening gets the half-written report rather than
        a separate account that could disagree with it. The stages are
        kept as a list with their timings: a round that took twenty
        seconds is a different thing to look at depending on whether
        nineteen of them were the model or the clustering.
        """
        now = time.perf_counter()
        stages = report.setdefault("stages", [])
        if stages:
            stages[-1]["seconds"] = round(now - stages[-1]["began"], 2)
        stages.append({"stage": stage, "detail": detail, "began": now,
                       "seconds": 0.0, "calls": 0, "tokens_in": 0,
                       "tokens_out": 0, "tokens_cached": 0,
                       "tokens_new": 0})
        report["stage"] = stage
        report["stage_detail"] = detail

    def _say_at_once(self, report: Dict[str, Any], running: int,
                     allowed: int, why: str) -> None:
        """How many calls are going at once, how many the computer has room
        for, and why - on the page beside what is being done."""
        report["at_once"] = {"running": running, "allowed": allowed, "why": why}

    def _given_up(self, where: Optional[_Where] = None) -> str:
        """Why the round stopped, in the words that fit which reason."""
        if self._stop.is_set():
            return ("The application is closing, so this round stopped where "
                    "it was. Nothing was written.")
        return (f"The {getattr(where, 'name', 'sandbox')} was emptied while "
                f"this round was running, so it stopped. Nothing it had found "
                f"is kept, because what it was about is gone.")

    def _going(self, where: Optional[_Where] = None) -> bool:
        """Whether this round should carry on.

        Two reasons it should not, and a round has to ask about both at
        every seam long enough to matter: the application is closing, and
        the sandbox it is working in has been emptied underneath it. The
        second is a button a person presses while watching the round they
        are abandoning, so it has to take effect while the round is still
        going rather than after it.
        """
        if self._stop.is_set():
            return False
        if where is not None:
            given_up = self._abandoned.get(where.scope)
            if given_up is not None and given_up.is_set():
                return False
        return True

    def _running_in(self, scope: str) -> Any:
        """The lock for one scope, made the first time it is asked for.

        `self._running` stays as the live one, because everything that
        reaches for it by name means the real round.
        """
        if str(scope) != SANDBOX:
            return self._running
        held = self._running_by.get(SANDBOX)
        if held is None:
            held = threading.Semaphore(1)
            self._running_by[SANDBOX] = held
        return held

    def abandon(self, scope: str = SANDBOX) -> None:
        """Stop whatever is running in this scope, as soon as it looks."""
        self._abandoned.setdefault(scope, threading.Event()).set()

    def carry_on(self, scope: str = SANDBOX) -> None:
        """Let the next round in this scope run."""
        given_up = self._abandoned.get(scope)
        if given_up is not None:
            given_up.clear()

    def _say_they_are_waiting(self, report: Dict[str, Any], kept: Any,
                              where: _Where) -> None:
        """Mark the tasks this round will ask about, before it asks.

        BY THE SAME RULE THE LOOP USES, not by a guess at it. The first
        version of this read `kept` and `described` off the layer entries
        - fields the summarising loop writes LATER - so every field was
        absent, every task looked new, and a task whose summary was about
        to be reused was told to say 'loading' and then never asked
        about. It would have read 'loading' until something changed in
        it, which on a quiet task is forever.
        """
        after = self._resummarise_after()
        try:
            live = list(kept.live())
        except Exception:            # pragma: no cover - a read, not a step
            return
        done = set(self._named_this_round())
        waiting = []
        for task in live:
            # ALREADY DONE IN THIS ROUND. Its name is this round's answer,
            # not a leftover, so it is neither re-marked nor re-asked.
            if task.id in done:
                continue
            # NEVER SUMMARISED, by the same signal the loop uses: the
            # summary RECORD. This asked whether the task had a title,
            # which is a copy kept for the page - the sandbox writes none
            # at all, so every rehearsal marked every task as owing a
            # description and then re-wrote all of them, round after
            # round. Marking has to agree with the decision it is
            # predicting, or it predicts something else.
            fresh = not bool(getattr(task, "summary_id", ""))
            if not fresh and not wants_summary(task, after):
                continue
            task.described = _waiting_on(
                task.described, len(getattr(task, "members", []) or []))
            waiting.append(task)
        report["waiting"] = [one.id for one in waiting]
        report["waiting_of"] = len(live)
        if not waiting:
            return
        try:
            kept.save(waiting)
        except Exception:            # pragma: no cover - a save, not a step
            log.debug("could not mark the waiting tasks", exc_info=True)

    def _by_urgency(self, report: Dict[str, Any]) -> List[int]:
        """The top tasks, most urgent first, ties keeping their order.

        The same rule the panel sorts by, so what a reader watches being
        filled in is the order they would have put it in themselves.
        """
        layers = report.get("layers") or []

        def mark(index: int) -> tuple:
            entry = layers[index]
            held = entry.get("summary") or {}
            described = held.get("described") or entry.get("described") or {}
            urgency = described.get("urgency")
            if urgency is None:
                urgency = held.get("urgency")
            try:
                number = int(urgency)
            except (TypeError, ValueError):
                number = 4
            # Negated so the most urgent sorts first; the index second,
            # so a tie keeps the order it already had rather than being
            # broken by some further invention.
            return (-number, index)

        return sorted(range(len(layers)), key=mark)

    def _summarise_inside(self, report: Dict[str, Any],
                          nodes: Sequence[_Node], trace_id: str,
                          where: _Where,
                          order: Optional[Sequence[int]] = None) -> None:
        """Summarise the sub-tasks of anything that is not itself a task.

        Lazily, and only there. Summarising every sub-task of every task
        would be a model call for each of thirty pieces to find the two
        that matter; summarising none of them means a real piece of work
        inside a state is never found. So the call is spent exactly where
        the answer was "this is a state" - which is the only place the
        answer could still be yes further down.
        """
        seats = {node.id: index for index, node in enumerate(nodes)}

        def inside(task: Dict[str, Any], depth: int) -> None:
            if depth > self.DEEPEST_SUMMARY:
                return
            for child in (task.get("children") or []):
                if len(child.get("states") or []) < self._min_nodes():
                    continue
                if child.get("summary"):
                    continue
                if not self._going(where):
                    return
                members = [seats[one] for one in child["states"]
                           if one in seats]
                if len(members) < self._min_nodes():
                    continue
                group = self._group(nodes, members, 0)
                group["id"] = child.get("id") or group["id"]
                self._at(report, "summarising what is inside",
                         f"{group['id']}, {group['size']} states - its parent "
                         f"had nothing to finish")
                summary = self._summarise(nodes, group, trace_id, where,
                                          self._vectors_now, report)
                self._spent(report, summary)
                child["summary"] = {
                    "title": summary.get("title", ""),
                    "summary": summary.get("summary", ""),
                    "reasoning": summary.get("reasoning", ""),
                    "evidence": list(summary.get("evidence") or []),
                    "record_id": summary.get("record_id", ""),
                    "kept": False,
                    "concrete": bool(summary.get("concrete", True)),
                    "concrete_why": summary.get("concrete_why", ""),
                    # The same as every other summary carries. Two places
                    # building this dict had already drifted, and the one
                    # here left out what it asked and what it was sent -
                    # so a sub-task found inside a state showed nothing.
                    "searched": bool(summary.get("searched")),
                    "searches": int(summary.get("searches", 0) or 0),
                    "looks": list(summary.get("looks") or []),
                    "prompt": summary.get("prompt", ""),
                    "answer": summary.get("answer", ""),
                    "calls": int(summary.get("calls", 0) or 0),
                    "tokens_in": int(summary.get("tokens_in", 0) or 0),
                    "tokens_out": int(summary.get("tokens_out", 0) or 0),
                    "tokens_cached": int(summary.get("tokens_cached", 0) or 0),
                    "tokens_new": int(summary.get("tokens_new", 0) or 0),
                }
                report.setdefault("summaries_inside", []).append({
                    "task": group["id"],
                    "title": summary.get("title", ""),
                    "concrete": bool(summary.get("concrete", True))})
                # Still not a task? Then look inside THIS one.
                if not summary.get("concrete", True):
                    inside(child, depth + 1)

        layers = report.get("layers") or []
        walk = list(order) if order is not None else list(range(len(layers)))
        for index in walk:
            if not 0 <= index < len(layers):
                continue
            task = layers[index]
            summary = task.get("summary") or {}
            if summary and not summary.get("concrete", True):
                inside(task, 1)

    def _name_the_pieces(self, report: Dict[str, Any], task: Dict[str, Any],
                         nodes: Sequence[_Node], trace_id: str,
                         where: _Where, stored: Any = None) -> None:
        """Summarise the sub-tasks of a task that IS shown.

        The other descent summarises what is inside a task that is NOT
        shown, looking for something worth showing. This is the opposite
        case and needs the same words for a different reason: the last
        layer is drawn as a tech tree, and a circle with no name on it is
        a circle.
        """
        seats = {node.id: index for index, node in enumerate(nodes)}
        # EVERY LAYER OF THIS TASK, BEFORE THE NEXT TASK BEGINS. This
        # named the immediate children and stopped, so with three layers
        # configured the third was never named at all: the picture opened
        # a piece and found circles with no words on them.
        #
        # Depth-first on purpose. The alternative - all the layer twos,
        # then all the layer threes - finishes no task until it has
        # finished every task, and a reader watching wants ONE piece of
        # work readable all the way down rather than every piece of work
        # half readable.
        pieces = list(task.get("children") or [])
        asked = set(self._tried_this_round())
        for child in pieces:
            if not self._going(where):
                continue
            # ITS INSIDES ARE QUEUED FIRST, whatever happens to this one.
            #
            # They were queued at the bottom of the loop, after this piece
            # had been described - so a piece that ALREADY had a name, and
            # was skipped a few lines down, never had its own pieces
            # queued at all. Two nested pieces under a named one were
            # counted as blank by the completeness check and never
            # reached by this loop, which kept the round open for good.
            if len(child.get("children") or []) >= 2:
                pieces.extend(child["children"])
            # ASKED ALREADY THIS ROUND. Recorded below and never read: the
            # only skip here was "already has a name", so a piece whose
            # answer came back unusable was asked again on every visit -
            # three model calls each time, for a round that could not
            # end because those same three pieces were still blank.
            if str(child.get("id") or "") in asked:
                continue
            # A SUMMARY WITH NO NAME IN IT IS NOT A NAME.
            #
            # This skipped any child that had a summary OBJECT, which is
            # not the same as having been named: a piece carried forward
            # from the store, or one whose last attempt produced nothing
            # usable, has the object and no title - and was then passed
            # over every round after, reading "preparing" for ever while
            # the panel said there was nothing left to run.
            if str((child.get("summary") or {}).get("title") or "").strip():
                continue
            members = [seats[one] for one in (child.get("states") or [])
                       if one in seats]
            if len(members) < self._min_nodes():
                continue
            group = self._group(nodes, members, 0)
            group["id"] = child.get("id") or group["id"]
            self._at(report, "naming a piece",
                     f"{group['id']}, {group['size']} states")
            # WHICH ONE, so the picture can light it and everything above
            # it. The top-level loop recorded this and the piece loop did
            # not, so during the whole of the piece-naming phase - which
            # is most of a round on a folder with real work in it -
            # nothing anywhere said "working".
            report["summarising"] = group.get("id") or ""
            if (report.get("plan") or {}).get("shape_only"):
                report["plan"] = self._the_shape_so_far(report, nodes)
            # ASKED, AND SAID SO - the same note the tasks above get.
            # Without it a piece whose answer came back empty stayed in
            # "left to try" for ever, and the round, which cannot finish
            # while something is left to try, was picked up every few
            # seconds to ask the same question of the same piece.
            summary = self._summarise(nodes, group, trace_id, where,
                                      self._vectors_now, report)
            # The same rule as the tasks above: only a call the model saw
            # counts as this piece's go - and a refused one ends the walk,
            # since every piece after it would be refused the same way.
            if summary.get("blocked"):
                report["blocked"] = True
                report["reason"] = summary["blocked"]
                break
            if where.scope != SANDBOX:
                self._also_tried(str(child.get("id") or ""))
            self._spent(report, summary)
            child["summary"] = {
                "title": summary.get("title", ""),
                "summary": summary.get("summary", ""),
                "concrete": bool(summary.get("concrete", True)),
                "concrete_why": summary.get("concrete_why", ""),
                "described": dict(summary.get("described") or {}),
                "described_now": list(summary.get("described_now") or []),
                "record_id": summary.get("record_id", ""),
                "kept": False, "evidence": list(summary.get("evidence") or []),
                "searched": bool(summary.get("searched")),
                "searches": int(summary.get("searches", 0) or 0),
                "looks": list(summary.get("looks") or []),
                "prompt": summary.get("prompt", ""),
                "answer": summary.get("answer", ""),
                # A sub-task's summary is a copy of the fields the page
                # needs rather than the whole thing, so anything added to
                # a summary has to be added here too or it exists for the
                # top layer only - which is how the prompt and the cost
                # came to be missing from every sub-task on the page.
                "exchange": list(summary.get("exchange") or []),
            }
            # And what is inside THIS piece, now, while the reader is
            # looking at it - down to the depth the grouping was told to
            # keep. `pieces` is extended rather than recursed into so the
            # order stays flat and visible: this piece, then its own
            # pieces, then the next piece of the task.
            # WRITTEN DOWN NOW, not when the task's last piece is done.
            #
            # The keeping happened once per TOP TASK, after all of its
            # pieces had been named - so a round interrupted part way
            # through lost every piece it had paid for. Seen on the live
            # agent: "2 of 17 sub-tasks" before a restart and "0 of 17"
            # after it, two model calls bought and thrown away.
            #
            # One write per piece instead of one per task is the same
            # row saved a few more times, which is nothing beside the
            # call that produced the name.
            if stored is not None:
                self._keep_the_pieces(task, stored)


    def _keep_the_pieces(self, entry: Dict[str, Any], task: Any) -> None:
        """Write a task's layers down with it.

        THE TASK OUTLIVES THE ROUND AND SO SHOULD ITS INSIDES. The pieces
        are worked out by the clustering and named by a call each, and
        none of that was saved: after a restart the picture had the tasks
        and nothing under them, so every circle opened onto "nothing
        inside this one divides" - which was not a finding, it was the
        round's working having been thrown away.

        Only what the picture reads: a name, a size, the states, the
        reason it stopped, and the same again for anything inside it. Not
        the prompts or the exchanges - those belong to the round that
        made them and are what the debugger's own report is for.
        """
        if task is None:
            return

        def row(node: Dict[str, Any]) -> Dict[str, Any]:
            held = node.get("summary") or {}
            return {
                "id": node.get("id"),
                "title": held.get("title", ""),
                "summary": held.get("summary", ""),
                "concrete": bool(held.get("concrete", True)),
                "described": dict(held.get("described") or {}),
                "states": list(node.get("states") or []),
                "why_closed": str(node.get("why_closed") or ""),
                "children": [row(kid) for kid in (node.get("children") or [])],
            }

        pieces = [row(kid) for kid in (entry.get("children") or [])]
        if not pieces:
            return
        held = dict(getattr(task, "described", {}) or {})
        held["pieces"] = pieces
        task.described = held
        try:
            if self._kept_now is not None:
                self._kept_now.save([task])
        except Exception:        # pragma: no cover - a save, not a step
            log.debug("could not keep the pieces of %s", task.id, exc_info=True)

    def _the_shape_so_far(self, report: Dict[str, Any],
                          nodes: Sequence[_Node]) -> Dict[str, Any]:
        """The tree as the clustering leaves it, before anything is named.

        THE SHAPE IS KNOWN BEFORE THE WORDS ARE. Grouping needs no model -
        it is distances and a cut - and the names are one model call per
        task after it. Waiting for the words before drawing anything meant
        the panel showed nothing for the whole of a round that takes
        minutes, and a reader cannot tell a round that is working from one
        that failed.

        So the circles, the spans and the arrangement go up as soon as
        they are known, each task showing whatever name it already has or
        saying it is loading, and every summary that lands redraws it one
        task further on. No edges here: a dependency is a judgement and
        the only thing that makes it is a model, so this says nothing
        about dependencies rather than guessing at them.
        """
        seats = {node.id: index for index, node in enumerate(nodes)}
        drawn = []
        for entry in (report.get("layers") or []):
            members = [nodes[seats[one]] for one in (entry.get("states") or [])
                       if one in seats]
            if not members:
                continue
            held = (entry.get("summary") or {})
            described = dict(held.get("described") or {})
            # NOT DRAWN ONCE THE MODEL HAS SAID IT IS NOT A TASK TO SHOW.
            # Until it has been summarised nobody knows, and a task that
            # has not been asked about yet is drawn - that is what
            # "loading" is for. After the answer comes back, a way of
            # working is the agent's own note about the user's habits and
            # does not belong in a picture of what they are in the middle
            # of.
            if held and held.get("concrete") is False:
                continue
            described = held.get("described") or {}
            # WHAT IT WAS CALLED LAST TIME, while it waits for this
            # round to reach it. The words are kept when a task is marked
            # as waiting - precisely so they are not lost - and the
            # picture was not using them: a task that had a name went
            # blank and said "loading" the moment a round decided to
            # describe it again, which reads as the name having been
            # thrown away rather than as it being rewritten.
            was = dict(described.get("was") or {}) if described else {}
            # The stored name, unless the store is holding the marker that
            # says this task is queued to be named again.
            named_now = _a_real_name(described)
            drawn.append({
                "id": entry.get("id"),
                # THE STORED ID AS WELL. `id` here is positional - the
                # first layer is `task_1` whatever task that happens to be
                # this round - and the emblem is generated from whatever it
                # is given. Seeded from the positional id, a task's emblem
                # follows its POSITION: re-sort the picture, or let a task
                # grow past another, and the badges swap over. The stored
                # id is the thing that lasts, which is what the emblem was
                # supposed to be drawn from.
                "task_id": str(entry.get("task_id") or ""),
                **_how_many_inside(entry),
                # BEING WORKED ON, OR HOLDING THE THING THAT IS. A
                # sub-task three layers down is still this task's work,
                # and a reader looking at the top layer should be able to
                # see where the time is going without opening anything.
                # Ids are built as `task_3.1.2`, so an ancestor is a
                # prefix - which is why they are compared that way.
                "working": _working_now(entry.get("id"), report),
                # What it is called: this round's name, or the one it had
                # before, or nothing if it has never had one.
                #
                # THE STORED TITLE IS NOT A NAME WHILE IT IS WAITING. A
                # task queued for rewriting has the word "loading" written
                # into its description - that is how the marking works -
                # so reading the stored title first meant drawing the
                # literal word and never reaching the name kept beside it.
                "title": (held.get("title") or named_now
                          or was.get("title") or ""),
                # WAITING IS A NOTE ON THE TASK, not the absence of a
                # name. A task keeps the name it was given until a better
                # one replaces it, so "has no name" and "is owed a new
                # description" became different questions the moment the
                # placeholder stopped being written into the title.
                "waiting": bool(described.get("waiting"))
                           or not bool(held.get("title") or named_now),
                # And the name shown is older than this round whenever
                # this round has not written one - whether it came from
                # the task itself or from the names kept beside it.
                "name_is_old": bool(not held.get("title")
                                    and (named_now or was.get("title"))),
                "size": len(members),
                # FROM ITS FIRST ACTIVITY TO ITS LAST, as its profile keeps
                # them: the states this round read of it are its recent
                # ones, and drawing it from those would cut its history off.
                "began": (entry.get("first_seen")
                          or min((str(one.starts_at or "") for one in members),
                                 default="")),
                "ended": (entry.get("last_seen")
                          or max((str(one.ends_at or "") for one in members),
                                 default="")),
                "count": entry.get("count") or len(members),
                "phase": entry.get("phase") or "active",
                "region": list(entry.get("region") or []),
                "stretches": list(entry.get("stretches") or []),
            })
        if len(drawn) < 2:
            return {}
        return {"nodes": drawn, "edges": [], "shape_only": True,
                "why": ("The arrangement is what the grouping found; the "
                        "names arrive one task at a time, and the arrows "
                        "once they are all named.")}

    def _plan_the_lot(self, report: Dict[str, Any], nodes: Sequence[_Node],
                      trace_id: str, where: _Where) -> Dict[str, Any]:
        """The active tasks themselves, in order, with what waits on what.

        `_plan` answers this for the pieces of one task. This asks it of
        the tasks, which is the same question one level up and needs the
        same instruction: when each began is arithmetic over its states,
        and which of them could not have been started until another was
        done is a judgement only the model can make.

        Only the tasks the user would be shown. A way of working is not a
        thing that can wait on another thing, and putting one in a
        dependency graph would invite the reader to draw a conclusion
        from it.
        """
        shown = [task for task in (report.get("layers") or [])
                 if (task.get("summary") or {}).get("concrete", True)]
        if len(shown) < 2:
            return {}
        # The same shape `_plan` builds from a task's children, so the one
        # renderer and the one prompt serve both.
        stand_in = {"id": "the active tasks", "children": shown,
                    "summary": {"title": "everything running now"}}
        return self._plan(stand_in, nodes, trace_id, where, report)

    def _plan(self, task: Dict[str, Any], nodes: Sequence[_Node],
              trace_id: str, where: _Where,
              report: Dict[str, Any]) -> Dict[str, Any]:
        """The sub-tasks of one task as a tech tree: when, and after what.

        Two things are needed that nothing else here knows. WHEN each piece
        ran is arithmetic on its states. WHICH PIECE COULD NOT HAVE BEEN
        DONE UNTIL ANOTHER WAS is a judgement, and only the model can make
        it - the times give the order things happened in, which is not the
        same thing at all and would draw a chain through five pieces that
        merely followed one another.

        So the times and the closures are computed and handed over as
        facts, and the model is asked for the edges and for which are
        finished. It is told plainly that most pairs have no edge, because
        a model asked for a graph will draw one.
        """
        children = list(task.get("children") or [])
        if len(children) < 2:
            return {}
        seats = {node.id: index for index, node in enumerate(nodes)}
        pieces = []
        for number, child in enumerate(children, start=1):
            members = [nodes[seats[one]] for one in (child.get("states") or [])
                       if one in seats]
            if not members:
                continue
            began = (child.get("first_seen")
                     or min((str(one.starts_at or "") for one in members),
                            default=""))
            ended = (child.get("last_seen")
                     or max((str(one.ends_at or "") for one in members),
                            default=""))
            closed = sum(1 for one in members
                         if str(one.status or "").lower() in ("done", "retired"))
            pieces.append({
                "number": number, "id": child.get("id"),
                "task_id": str(child.get("task_id") or ""),
                **_how_many_inside(child),
                # CARRIED HERE AS WELL. The early shape marks what is
                # being worked on, and this replaces that shape once the
                # names are in - so without it the pulse stopped at the
                # moment the finished drawing arrived, which is while the
                # round is still running.
                "working": _working_now(child.get("id"), report),
                # ITS OWN NAME OR NONE - NEVER A STATE'S.
                #
                # A task with no summary took the title of its first
                # state, which is a sentence about the user ("The user is
                # adding an example...") rather than a name for the work.
                # It also made the task look named: the plan a round
                # drew said "8 of 8", the stored rows behind it said "5
                # of 8", and the panel flipped between the two every time
                # a visit started or ended. A state is never shown as if
                # it were a task. A task without a name says so, and the
                # page has words for that.
                "title": str((child.get("summary") or {}).get("title")
                             or ""),
                "size": len(members), "began": began, "ended": ended,
                "closed": closed, "of": len(members),
            })
        if len(pieces) < 2:
            return {}

        lines = [f"{len(pieces)} pieces of one task"
                 f"{': ' + (task.get('summary') or {}).get('title', '') if (task.get('summary') or {}).get('title') else ''}",
                 ""]
        for piece in pieces:
            lines.append(f"--- {piece['number']} ---")
            lines.append(f"what it is: {piece['title']}")
            lines.append(f"states: {piece['size']}, of which "
                         f"{piece['closed']} are closed")
            lines.append(f"ran: {piece['began'] or '?'} to "
                         f"{piece['ended'] or '?'}")
            lines.append("")
        asked = "\n".join(lines)

        # Defined here, used twice: once to send and once to show. It was
        # written inline at the call and named at the page, so `instruction`
        # was a name nothing had bound - every round raised NameError here,
        # the round's own `except` turned it into `report["error"]`, and the
        # tech tree simply never appeared. Nothing failed loudly enough to
        # notice, which is why a round now asserts it finished clean.
        instruction = PLAN_PROMPT
        self._at(report, "putting the pieces in order",
                 f"{len(pieces)} sub-tasks of {task.get('id')}")
        self._show_the_prompt(report, asked, instruction=instruction,
                              waiting_for="the order and what is finished")
        # THE SAME QUESTION IS ANSWERED ONCE. The plan of the whole lot is
        # drawn again on every visit that reaches the end of a round - two
        # seconds apart while one is open - and nothing marks it asked, so
        # each of them was another Opus call of twelve thousand tokens and
        # eight and a half seconds about tasks whose names, sizes and dates
        # had not moved. What is sent is the whole of what the answer can
        # depend on, so the answer to the same text is kept and read again;
        # a change to any task in it is a different text and is asked.
        # Only an answer that read as JSON is kept: one that did not is
        # asked again next time, as it always was.
        plans = self._plans_answered
        asked_as = (instruction, asked)
        answered_before = plans.get(asked_as)
        if answered_before is not None:
            reply, answered, at = answered_before
            answered = json.loads(json.dumps(answered))
        else:
            try:
                response = self.models.invoke(
                    ModelRequest(messages=[{"role": "user", "content": asked}],
                                 system=instruction,
                                 max_tokens=1024, task="state_heartbeat",
                                 language=in_display("`why`"),
                                 metadata={"task": task.get("id"),
                                           "pieces": len(pieces)}),
                    role="heartbeat")
            except ModelUnavailable as exc:
                return {"blocked": str(exc), "prompt": asked, "nodes": pieces}
            reply = getattr(response, "text", "") or ""
            answered = _as_json(reply) or {}
            at = datetime.now(timezone.utc).isoformat()
            if answered:
                with self._plans_lock:
                    while len(plans) >= self.PLANS_KEPT:
                        plans.pop(next(iter(plans)))
                    plans[asked_as] = (reply, json.loads(json.dumps(answered)),
                                       at)
            self._spent(report, {"calls": 1,
                                 "tokens_in": int(getattr(getattr(
                                     response, "usage", None), "input_tokens", 0) or 0),
                                 "tokens_out": int(getattr(getattr(
                                     response, "usage", None), "output_tokens", 0) or 0),
                                 "tokens_cached": int(getattr(getattr(
                                     response, "usage", None),
                                     "cached_input_tokens", 0) or 0),
                                 "tokens_new": int(getattr(getattr(
                                     response, "usage", None),
                                     "cache_written_tokens", 0) or 0)})

        by_number = {piece["number"]: piece for piece in pieces}
        edges = []
        for pair in (answered.get("depends") or []):
            try:
                after, before = int(pair[0]), int(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
            if after == before or after not in by_number or before not in by_number:
                continue
            edges.append({"after": by_number[after]["id"],
                          "before": by_number[before]["id"]})
        done = set()
        for number in (answered.get("finished") or []):
            try:
                done.add(int(number))
            except (TypeError, ValueError):
                continue
        for piece in pieces:
            piece["finished"] = piece["number"] in done
        out = {"nodes": pieces, "edges": edges,
               "why": str(answered.get("why") or ""),
               "prompt": asked, "answer": reply,
               # The same shape a summary's exchange has, so the same
               # writer keeps it. This is a second model call about the
               # same task, asking a question the summary never asked -
               # what order these were done in, and which are finished -
               # and its answer is what the tech tree is drawn from. A
               # reader wondering why an arrow points the way it does has
               # nowhere else to look.
               "calls": 1,
               "exchange": [{
                   "call": 1,
                   "instruction": instruction,
                   "sent": asked,
                   "reply": reply,
                   "read_as": answered,
                   "was_json": bool(answered),
                   # It is not offered a search here, so this is not a
                   # decision it made - it is a fact about the call, and
                   # the file should not read as though it declined.
                   "asked_to_search": False,
                   "what_it_asked_for": None,
                   "at": at,
               }],
               "why_no_search": ("This call was not offered a search: it is "
                                 "shown the pieces of one task and asked "
                                 "only what order they went in."),}
        return out

    def _shown(self, report: Dict[str, Any],
               nodes: Sequence[_Node]) -> List[Dict[str, Any]]:
        """The tasks to put in front of the user, found by descending.

        THE LAYER THAT ORGANISES IS NOT THE LAYER THAT IS SHOWN. A top
        task can be a state with nothing to finish - "clearing out the
        inbox" - while one of the sub-tasks inside it is a real piece of
        work with an end: the visa letter that happened to be answered by
        email. Showing the top layer hides that; showing every layer
        drowns it.

        So the tree is walked from the top, and the first thing on each
        branch that has progress to track is what gets shown - lifted to
        the front regardless of how deep it was found, with a note of what
        it was lifted out of. A branch where nothing at any depth has an
        end shows nothing, and the whole of it stays in the application's
        own notes.
        """
        shown: List[Dict[str, Any]] = []

        def walk(task: Dict[str, Any], came_from: str, depth: int) -> None:
            summary = task.get("summary") or {}
            if not summary:
                # Never summarised, so nothing was judged: too small to be
                # worth a call, and too small to show.
                return
            if summary.get("concrete", True):
                shown.append({
                    "id": task.get("id"),
                    "task_id": task.get("task_id"),
                    "title": summary.get("title", ""),
                    "summary": summary.get("summary", ""),
                    "size": task.get("size", 0),
                    "found_at_depth": depth,
                    "lifted_out_of": came_from,
                    "why": summary.get("concrete_why", ""),
                })
                return
            # Not a task itself. Whatever is inside it might be.
            for child in (task.get("children") or []):
                walk(child, came_from or task.get("id", ""), depth + 1)

        for task in (report.get("layers") or []):
            walk(task, "", 0)
        return shown

    @staticmethod
    def _label(report: Dict[str, Any], index: int,
               summary: Dict[str, Any]) -> None:
        """Put the summary on the task, not only on the group under it.

        The page draws the tasks in `layers`; the summary was being set on
        the community beneath, which is the same grouping under a
        different name. So every task was drawn with no title at all, and
        had been from the first version of this - the words were written,
        stored, sent to the page, and read from a field nothing had ever
        put them in.
        """
        tasks = report.get("layers") or []
        if not (0 <= index < len(tasks)):
            return
        tasks[index]["summary"] = {
            "title": summary.get("title", ""),
            "summary": summary.get("summary", ""),
            "reasoning": summary.get("reasoning", ""),
            "evidence": list(summary.get("evidence") or []),
            "record_id": summary.get("record_id", ""),
            "kept": bool(summary.get("kept")),
            "why": summary.get("why", ""),
            "concrete": bool(summary.get("concrete", True)),
            "concrete_why": summary.get("concrete_why", ""),
            "described": dict(summary.get("described") or {}),
            "urgency": int(summary.get("urgency", 4) or 4),
            "importance": int(summary.get("importance", 4) or 4),
            "weighed_why": summary.get("weighed_why", ""),
            # Which parts THIS round wrote, so the page can show what moved
            # rather than showing four paragraphs with no sign of which of
            # them is new.
            "described_now": list(summary.get("described_now") or []),
            "changed_why": summary.get("changed_why", ""),
            "searched": bool(summary.get("searched")),
            "searches": int(summary.get("searches", 0) or 0),
            "joins": int(summary.get("joins", 0) or 0),
            "reads_as_a_list": bool(summary.get("reads_as_a_list")),
            # The searches themselves, not only how many. The page says on
            # the task whether it looked anything up, and a count with no
            # searches under it cannot show what was asked or what came
            # back - which is the half that answers whether it should
            # have believed the answer.
            "looks": list(summary.get("looks") or []),
            "prompt": summary.get("prompt", ""),
            "seen": dict(summary.get("seen") or {}),
            "exchange": list(summary.get("exchange") or []),
        }

    @staticmethod
    def _spent(report: Dict[str, Any], summary: Dict[str, Any]) -> None:
        """Add what a model call cost to the step that made it.

        Per step rather than per round, because the two answer different
        questions: a round that took forty seconds and thirty thousand
        tokens tells you nothing about which part to make cheaper.
        """
        stages = report.get("stages") or []
        if not stages:
            return
        here = stages[-1]
        here["calls"] = int(here.get("calls", 0)) + int(summary.get("calls", 0))
        here["tokens_in"] = (int(here.get("tokens_in", 0))
                             + int(summary.get("tokens_in", 0) or 0))
        here["tokens_out"] = (int(here.get("tokens_out", 0))
                              + int(summary.get("tokens_out", 0) or 0))
        here["tokens_cached"] = (int(here.get("tokens_cached", 0))
                                 + int(summary.get("tokens_cached", 0) or 0))
        here["tokens_new"] = (int(here.get("tokens_new", 0))
                              + int(summary.get("tokens_new", 0) or 0))

    def _as_of(self, held: Dict[str, Any]) -> Dict[str, Any]:
        """Which states this summary was made from, said in plain time.

        WHAT A READER NEEDS FROM A SUMMARY IS HOW OLD IT IS. A round
        reads up to a line it draws before it starts, and everything
        after that line is in the next round - so "these are your tasks"
        is really "these are your tasks as of a quarter past ten this
        morning", and the difference matters as soon as the morning has
        moved on.

        The running round's own line is preferred where there is one,
        because that is the answer for what is being drawn right now; the
        last completed round's line otherwise.
        """
        record = self._round_record()
        line = str(held.get("reads_until") or record["reads_until"] or "")
        return {
            "reads_until": line,
            "said": self._their_clock(line) if line else "",
            "finished": record["completed_at"],
            "finished_said": (self._their_clock(record["completed_at"])
                              if record["completed_at"] else ""),
            "next_said": self._when_the_next_round_starts(record),
            "running": bool(held.get("running")),
            "done": bool(record["done"]),
        }

    def _when_the_next_round_starts(self, record: Dict[str, Any]) -> str:
        """When the hours run out, in the reader's own clock - or "".

        Only for a round that is finished with: one still open is carried
        on in seconds, not hours, and giving it a start time hours away
        would say it is waiting when it is not. The hours are measured
        from when the last round stopped working, which is the same
        moment the gate measures from - so this is the time the gate will
        say yes, not an estimate of it.
        """
        if not (record.get("done") or record.get("exhausted")):
            return ""
        stopped = str(record.get("completed_at") or "")
        if not stopped:
            return ""
        try:
            when = datetime.fromisoformat(stopped)
        except ValueError:
            return ""
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        starts = when + timedelta(hours=self._every_hours())
        return self._their_clock(starts.isoformat())

    def _last_known_names(self) -> Dict[str, Dict[str, Any]]:
        """What each saved task was called, and what was inside it.

        A ROUND STARTING MUST NOT BLANK THE PICTURE. Before one begins the
        panel is drawn from the saved tasks, which carry the names and the
        pieces the last round worked out. The moment a round starts, the
        drawing switches to ITS report - where nothing has been named yet
        and the pieces are not computed until later - so every name the
        reader was looking at disappeared at once and came back one at a
        time over the next few minutes.

        The new round's answers are better than the old ones and win
        wherever it has them. Where it has nothing yet, the last round's
        words are what there is to show, and showing them is what a reader
        means by the picture not flickering.
        """
        out: Dict[str, Dict[str, Any]] = {}
        try:
            for task in Kept(self.db, LIVE).live():
                described = dict(getattr(task, "described", {}) or {})
                name = _a_real_name(described) or str(
                    (described.get("was") or {}).get("title") or "")
                out[task.id] = {
                    "title": name,
                    "pieces": list(described.get("pieces") or []),
                    # THE WEIGHTS TOO. A round reports the default 4 for
                    # a task it has not reached, and 4 is also a real
                    # answer - so mid-round every unreached task read
                    # "urgency 4/7 · importance 4/7", which is not what it
                    # was given and not nothing either. The weight it
                    # already has is the truth until this round writes a
                    # better one.
                    "urgency": described.get("urgency"),
                    "importance": described.get("importance"),
                    "weighed_how": described.get("weighed_how"),
                    "weighed_why": described.get("weighed_why"),
                    # AND ITS NEXT STEPS, which the user edits between
                    # rounds: the round's copy is a photograph of when it
                    # was taken, and a step added a moment ago is not in it.
                    "todo": _todo.drawn(described),
                }
        except Exception:        # pragma: no cover - a read, not a step
            log.debug("could not read the last known names", exc_info=True)
        return out

    def _tasks_now(self) -> int:
        """How many live tasks there are, as the rows have them."""
        try:
            return sum(1 for task in Kept(self.db, LIVE).live()
                       if (task.described or {}).get("concrete", True))
        except Exception:        # pragma: no cover - a read, not a step
            return 0

    def task_history(self, task_id: str) -> Optional[Dict[str, Any]]:
        """What a task has been: its earlier names and descriptions, newest
        first, and the tasks it came from - split from, merged from, taken
        back out of - each with what it was called and where it came from
        in turn."""
        kept = {}
        for scope in (LIVE, SANDBOX):
            for one in Kept(self.db, scope).by_ids([task_id]):
                kept = {"task": one, "scope": scope}
        if not kept:
            return None
        task, scope = kept["task"], kept["scope"]

        def said(one: Task) -> Dict[str, Any]:
            held = one.described or {}
            was = held.get("was") or {}
            return {"id": one.id,
                    "title": _a_real_name(held) or str(was.get("title") or ""),
                    "summary": str(held.get("summary") or "")[:600],
                    "at": one.summary_at, "size": one.size,
                    "retired_at": one.retired_at,
                    "born_of": dict(one.born_of or {})}

        def came_from(one: Task, depth: int) -> List[Dict[str, Any]]:
            born = one.born_of or {}
            ids = [str(x) for x in (born.get("from") or []) if x]
            if born.get("was"):
                ids.append(str(born["was"]))
            if not ids or depth >= 4:
                return []
            out = []
            for parent in Kept(self.db, scope).by_ids(list(dict.fromkeys(ids))):
                out.append({**said(parent), "how": str(born.get("kind") or ""),
                            "why": str(born.get("why") or ""),
                            "came_from": came_from(parent, depth + 1)})
            return out

        now = said(task)
        return {"task": now,
                "versions": list((task.described or {}).get("history") or []),
                "came_from": came_from(task, 0)}

    #: Where the last round's drawing is kept between runs of the app.
    DRAWING_KEY = "state_heartbeat.last_drawing"

    def _keep_the_drawing(self, report: Dict[str, Any]) -> None:
        """The round's drawing, kept as it was drawn.

        THE SAME PICTURE AFTER A RESTART. The round's report dies with the
        process, and the picture was then rebuilt from the task rows alone -
        no arrows between tasks, no pieces inside them - so closing the app
        and opening it again showed a different drawing of the same tasks.
        What the picture reads from the report is kept, and read again
        until the next round replaces it.
        """
        if self.db is None:
            return
        kept = {"plan": report.get("plan") or {},
                "layers": report.get("layers") or [],
                "states": [{"id": one.get("id"), "status": one.get("status"),
                            "starts_at": one.get("starts_at", ""),
                            "ends_at": one.get("ends_at", "")}
                           for one in report.get("states") or []
                           if isinstance(one, dict)],
                "finished_at": report.get("finished_at", ""),
                "where": report.get("where") or {}}
        try:
            self.db.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (self.DRAWING_KEY, json.dumps(kept, default=str)))
            self._kept_drawing = kept
        except Exception:        # pragma: no cover - a picture, not a record
            log.debug("could not keep the drawing", exc_info=True)

    def _the_kept_drawing(self) -> Dict[str, Any]:
        """The last round's drawing, from where it was kept."""
        if self._kept_drawing is None:
            self._kept_drawing = {}
            if self.db is not None:
                try:
                    row = self.db.query_one("SELECT value FROM kv WHERE key = ?",
                                            (self.DRAWING_KEY,))
                    if row:
                        self._kept_drawing = json.loads(row["value"]) or {}
                except Exception:    # pragma: no cover - a read
                    log.debug("could not read the kept drawing", exc_info=True)
        return self._kept_drawing

    def _from_the_store(self) -> Dict[str, Any]:
        """The picture built from the saved tasks, with no round involved.

        THE TASKS OUTLIVE THE ROUND THAT FOUND THEM. They are rows in a
        table - that is the whole point of keeping them - and the report
        that found them is in memory and dies with the process. So after a
        restart the panel was reading an empty report and saying "no
        grouping to draw yet" over a database holding nine tasks and five
        hundred states, which is the opposite of true and reads as the
        grouping having failed.

        What is NOT here is the layers below the top: the pieces of a task
        are worked out while a round runs and are not saved, so this draws
        the tasks and says that the insides come with the next round rather
        than showing a circle that opens onto nothing.
        """
        try:
            tasks = Kept(self.db, LIVE).live()
        except Exception:        # pragma: no cover - a read, not a step
            # LOUDLY. This was a debug line, so a read that failed while
            # a round was writing came back as "no tasks" and the panel
            # drew the empty-folder message over a folder with nine tasks
            # in it. An empty answer that means "could not look" has to
            # be distinguishable from one that means "nothing there".
            log.warning("could not read the saved tasks", exc_info=True)
            return {}
        if len(tasks) < 2:
            return {}
        held = {}
        try:
            # FOUR COLUMNS, not every field of every state. This read the
            # whole row - titles, details, evidence, metadata - to use the
            # span and the status: 461ms and 2.2MB over 786 states, on
            # every poll of a panel somebody leaves open, which is what
            # made starting up feel slow.
            for one in self.states.when_they_ran(OBSERVED):
                held[one["id"]] = one
        except Exception:        # pragma: no cover - a read, not a step
            log.debug("could not read the states behind the tasks",
                      exc_info=True)
        nodes, layers = [], []
        for task in sorted(tasks, key=lambda one: getattr(one, "seat", 0) or 0):
            # SENT, AND MARKED. Left out here, this drawing and the one
            # built from a round showed different folders - which is what
            # a restart looked like losing five names.
            rejected = (getattr(task, "described", {}) or {}
                        ).get("concrete") is False
            members = [held[one] for one in (getattr(task, "members", []) or [])
                       if one in held]
            spans = [(str(one.get("starts_at") or ""),
                      str(one.get("ends_at") or "")) for one in members]
            described = dict(getattr(task, "described", {}) or {})
            was = dict(described.get("was") or {})
            title = _a_real_name(described) or str(was.get("title") or "")
            # Named here because the node below counts them and the layer
            # further down turns them back into rows.
            saved_rows = (list(described.get("pieces") or [])
                          if self._pieces_named() else [])
            began = (task.first_seen
                     or min((one[0] for one in spans), default=""))
            ended = (task.last_seen
                     or max((one[1] for one in spans), default=""))
            nodes.append({"id": task.id, "task_id": task.id, "seat": task.seat,
                          "title": title,
                          "hidden": rejected,
                          "waiting": not title,
                          **_how_many_inside({"children": saved_rows}),
                          "size": len(getattr(task, "members", []) or []),
                          "count": task.count,
                          "phase": task.phase(),
                          "region": task.region(_specificity(tasks)),
                          "places": task.weighed_places(_specificity(tasks)),
                          "weight": round(float(
                              task.profile.get("weight") or 0.0), 1),
                          "stretches": task.stretches(),
                          "began": began, "ended": ended})
            saved = saved_rows

            def back(row: Dict[str, Any]) -> Dict[str, Any]:
                """One saved piece, in the shape the drawing reads."""
                return {
                    "id": row.get("id"),
                    "size": len(row.get("states") or []),
                    "states": list(row.get("states") or []),
                    "why_closed": str(row.get("why_closed") or ""),
                    "summary": {"title": row.get("title", ""),
                                "summary": row.get("summary", ""),
                                "concrete": bool(row.get("concrete", True)),
                                "described": dict(row.get("described") or {})},
                    "children": [back(kid) for kid in
                                 (row.get("children") or [])],
                }

            layers.append({
                "id": task.id,
                "size": len(getattr(task, "members", []) or []),
                "states": list(getattr(task, "members", []) or []),
                "why_closed": ("" if saved else
                               "Its pieces have not been worked out yet - "
                               "the next round names them."),
                "summary": {"title": title,
                            "summary": str(described.get("summary") or ""),
                            "concrete": bool(described.get("concrete", True)),
                            "described": described,
                            "urgency": described.get("urgency"),
                            "importance": described.get("importance")},
                "children": [back(row) for row in saved],
            })
        return {
            "plan": {"nodes": nodes, "edges": [], "shape_only": True,
                     "from_store": True,
                     "why": ("Drawn from the saved tasks. The arrows and the "
                             "pieces inside each task come from a round, "
                             "and the last one's working is not kept.")},
            "layers": layers,
            "states": [dict(one) for one in held.values()],
            # The projects too: a restart should not undo a grouping the
            # model was asked about and agreed to.
            "projects": self._projects_now(),
            "from_store": True,
            "as_of": self._as_of({}),
        }

    def the_picture(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """What the page draws: every task with more work in it than a task
        holds, and nothing else. Not hidden - not there, so "show the
        rejected groupings" does not bring back an afternoon's errand.

        With tasks and projects (section 34.5): the projects, each with how
        long it was worked on - the page filters them by that and by when."""
        if self._projects_scheme():
            return self.projects_picture()
        picture = self._the_whole_picture(*args, **kwargs)
        return self._without_the_short(picture)

    def projects_picture(self, scope: str = LIVE, tasks_each: int = 1000
                         ) -> Dict[str, Any]:
        """The projects of the per-run scheme, newest work first: the live
        ones and the dormant ones (the page decides what its time range
        shows), each with its latest tasks; the round's state beside."""
        from .project_round import is_scheme_row, project_view
        from .task_projects import RunTasks

        run_tasks = RunTasks(self.db, scope)
        kept = Kept(self.db, scope)
        # Every one this scheme made - live, dormant, and ended in a merge or
        # a split (the debugger draws where they went; the Overview leaves
        # the ended out).
        rows = [one for one in kept.all(limit=5000) if is_scheme_row(one)]
        rows.sort(key=lambda one: one.last_seen, reverse=True)
        # WHETHER A RUN IS DUE only while there is nothing to show: it is
        # said only then, and it counts every state recorded - on the first
        # load after the computer starts, read off the disk, seconds.
        due: Dict[str, Any] = {}
        if not rows:
            try:
                due = self.due_now()
            except Exception:            # pragma: no cover - a read
                due = {}
        return {"scheme": "projects",
                "projects": [project_view(one, run_tasks, tasks_each) for one in rows],
                "tasks_written": run_tasks.count(),
                "due": due,
                "running": bool((self._progress or {}).get("running")),
                "last": {key: (self._last_report or {}).get(key) for key in (
                    "finished_at", "states_read", "calls", "note", "blocked", "error",
                    "projects", "switched")}}

    def _without_the_short(self, picture: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(picture, dict):
            return picture
        plan = dict(picture.get("plan") or {})
        nodes = list(plan.get("nodes") or [])
        try:
            kept = {task.id: task for task in Kept(self.db, LIVE).live()
                    + Kept(self.db, LIVE).archived()}
        except Exception:        # pragma: no cover - a read, not a step
            return picture
        short = {task_id for task_id, task in kept.items()
                 if self._too_short(task)}
        if not short:
            return picture
        gone = {str(node.get("id")) for node in nodes
                if str(node.get("task_id") or node.get("id")) in short}
        out = dict(picture)
        plan["nodes"] = [node for node in nodes if str(node.get("id")) not in gone]
        plan["edges"] = [edge for edge in plan.get("edges") or []
                         if str(edge.get("before")) not in gone
                         and str(edge.get("after")) not in gone]
        out["plan"] = plan
        out["layers"] = [layer for layer in picture.get("layers") or []
                         if str(layer.get("id")) not in gone
                         and str(layer.get("task_id") or "") not in short]
        projects = []
        for project in picture.get("projects") or []:
            project = dict(project)
            project["tasks"] = [one for one in project.get("tasks") or []
                                if one not in short]
            if project["tasks"]:
                projects.append(project)
        if "projects" in picture:
            out["projects"] = projects
        out["too_little_work"] = len(gone)
        return out

    def _the_whole_picture(self) -> Dict[str, Any]:
        """The drawing, and never a blank one while a drawing exists.

        See `_the_picture_now`. This wrapper is about one thing: a
        picture that came back empty when the last one was not. The panel
        reads an empty plan as "nothing has been grouped yet" and says so
        - over a folder with nine tasks in it - because the two cases
        arrive looking identical. They are not the same thing, and the
        reader watching the drawing vanish and come back is being told
        something untrue twice.
        """
        drawn = self._the_picture_now()
        has_rows = len(((drawn or {}).get("plan") or {}).get("nodes") or [])
        if has_rows:
            self._last_good_picture = drawn
            return drawn
        held = getattr(self, "_last_good_picture", None)
        if not held:
            return drawn
        # The shape from before, with whatever is true NOW about the
        # round - so it is last moment's drawing, not last moment's news.
        out = dict(held)
        out["round"] = self._round_record()
        out["due"] = self.due_now()
        out["at_work"] = bool((self.progress() or {}).get("running"))
        out["held_over"] = True
        return out

    def _the_picture_now(self) -> Dict[str, Any]:
        """Just enough of the last round to draw the tree.

        THE WHOLE REPORT IS SEVEN MEGABYTES on a real folder - every
        state, every prompt, every exchange, kept so the debugger can
        show what was asked and answered. That is the right payload for
        a page somebody opens to investigate a round and the wrong one
        for a tab they open twenty times a day, so this is the same
        drawing's worth of it: the plan, the tasks with their names and
        weights, and for each state only the five fields a row needs.

        Same key names as the report, so the picture is drawn by the
        same function from either - a second drawing function fed a
        second shape is how the two would drift.
        """
        # THE ROUND THAT IS HAPPENING, IF ONE IS. `last_report` is written
        # when a round ENDS, so during one - which is minutes - this was
        # serving the previous round's picture: the shape drawn early
        # never appeared, the names never filled in one at a time, and the
        # task being worked on never said so. All three of those exist
        # only while a round runs, which is exactly when this was looking
        # somewhere else.
        live = self.progress() or {}
        held = (live if live.get("running")
                else (self.last_report or self._the_kept_drawing() or {}))
        # NO ROUND IN MEMORY IS NOT NO GROUPING. A restart empties the
        # report and leaves every task where it was - in the database -
        # so the picture comes from there instead of claiming there is
        # nothing to draw.
        # THE SAVED TASKS FILL THE GAP, INCLUDING WHILE A ROUND RUNS.
        #
        # A round reads its states and finds its groups before it can draw
        # anything, and that takes a moment on a real folder. Refusing the
        # saved tasks during it - which is what this did for a while -
        # leaves the panel saying "no grouping to draw yet" over a database
        # full of tasks, at the exact moment the reader has just been told
        # a round is running. An empty picture is the one thing the panel
        # must not show while there is something to show.
        #
        # The reason for refusing them was that the stored copy of a task
        # queued for rewriting says "loading". That is fixed where it
        # belongs: the name it had is kept beside the marker and is what
        # gets drawn, so the saved tasks now read as last round's answer
        # rather than as a row of blanks.
        # BETWEEN VISITS, THE ROWS ARE THE TRUTH.
        #
        # This drew the last ROUND's plan whenever that plan had anything
        # in it, and a round's plan is a photograph of the moment it
        # ended. The task rows have moved on since - a visit names three
        # more and stops, and the panel goes on showing the count from
        # the round before, so progress appears to go backwards when the
        # next visit finally replaces it. The store is built from the
        # rows themselves, so it is current by construction.
        #
        # While a visit IS running its own report is the better source:
        # it holds the half-finished shape, which is the thing worth
        # watching.
        # The round's own plan has the arrows and the pieces in it, which
        # the rows alone do not, so it stays the first choice. It is only
        # set aside when the rows have come to hold MORE tasks than it
        # knows about, which means the grouping moved on after it was
        # drawn and its count is no longer about the same picture.
        drawn_now = len((held.get("plan") or {}).get("nodes") or [])
        resting = not bool((self.progress() or {}).get("running"))
        if drawn_now < 2 or (resting and self._tasks_now() > drawn_now):
            saved = self._from_the_store()
            if saved:
                saved["running"] = bool(held.get("running"))
                saved["round"] = self._round_record()
                saved["due"] = self.due_now()
                saved["waiting"] = list(held.get("waiting") or [])
                # THE SAME LIVE FACTS AS THE OTHER WAY OUT.
                #
                # This is the path taken whenever the drawing comes from
                # the store, which is most of the time: after a restart,
                # and between rounds. Anything added to the full picture
                # below and not added here is simply absent from what the
                # page actually receives - `at_work` and the stage were,
                # so the panel could not tell a visit in progress from an
                # open round at rest no matter what the agent was doing.
                saved["at_work"] = bool(
                    (self.progress() or {}).get("running"))
                saved["stage"] = str((self.progress() or {}).get("stage") or "")
                saved["stage_detail"] = str(
                    (self.progress() or {}).get("stage_detail") or "")
                saved["summarising"] = str(held.get("summarising") or "")
                saved["summarising_all"] = [str(one) for one in
                                            held.get("summarising_all") or []]
                saved["at_once"] = dict(held.get("at_once") or {})
                saved["work"] = self._how_far_through()
                return saved

        # What the last round called things, for anything this one has not
        # reached yet.
        # ALWAYS, NOT ONLY MID-ROUND. The names on the rows are the
        # newest there are: a visit writes each one as it lands and then
        # stops, and the round's plan keeps whatever it held when it was
        # drawn. Overlaying only while a round ran meant the panel went
        # back to the older names the moment the visit ended - progress
        # appearing to undo itself between visits.
        known = self._last_known_names()
        hidden = self._not_worth_drawing()
        leisure = self._leisure_tasks()

        def trim(node: Dict[str, Any]) -> Dict[str, Any]:
            summary = node.get("summary") or {}
            # The name this round wrote, or the one it had before.
            was = known.get(str(node.get("task_id") or ""), {})
            title = summary.get("title", "") or ""
            pieces = [trim(kid) for kid in (node.get("children") or [])]
            if not title and was.get("title"):
                title = was["title"]
            if not pieces and was.get("pieces") and self._pieces_named():
                # The sub-tasks the last round worked out. This round will
                # replace them when it gets there; until then they are the
                # only account of what is inside this task that anybody has.
                pieces = [{"id": one.get("id"), "size": len(one.get("states") or []),
                           "states": list(one.get("states") or []),
                           "why_closed": str(one.get("why_closed") or ""),
                           "summary": {"title": one.get("title", ""),
                                       "summary": one.get("summary", ""),
                                       "concrete": bool(one.get("concrete", True)),
                                       "described": dict(one.get("described") or {})},
                           "children": []}
                          for one in was["pieces"]]
            return {
                "id": node.get("id"),
                "size": node.get("size"),
                "states": list(node.get("states") or []),
                "why_closed": summary.get("why_closed")
                              or node.get("why_closed") or "",
                "summary": {
                    "title": title,
                    "summary": summary.get("summary", ""),
                    "concrete": bool(summary.get("concrete", True)),
                    # The next steps from the row, always: see
                    # `_last_known_names`.
                    "described": dict(_weighed_as(summary.get("described"),
                                                  was), **(was.get("todo")
                                                           or {})),
                    # THE TASK'S OWN DESCRIPTION FIRST. The round's copy
                    # always carries a number - 4 where nothing was given,
                    # which is also a real answer - so asking it first
                    # means a task the round has not reached reports the
                    # default instead of the weight it was given weeks
                    # ago. `described` is the stored answer for a task
                    # whose summary was kept and the new one for a task
                    # just written, which is right in both cases.
                    "urgency": _either(
                        (summary.get("described") or {}).get("urgency"),
                        _either(was.get("urgency"), summary.get("urgency"))),
                    "importance": _either(
                        (summary.get("described") or {}).get("importance"),
                        _either(was.get("importance"),
                                summary.get("importance"))),
                },
                "children": pieces,
            }

        plan = held.get("plan") or {}
        return {
            # The drawn rows get the same treatment: a node this round has
            # not named yet shows what the last round called it, marked as
            # last round's so the pulse and the panel can say so.
            # NOT WORTH SHOWING MEANS NOT SHOWN.
            #
            # A summary decides whether its grouping is a task somebody
            # would recognise, and the drawing built from the stored rows
            # has always left out the ones that are not. The drawing built
            # from a round's own report did not - so the panel showed
            # nine tasks while a round was in memory and four after a
            # restart, and the five that went were read as five names
            # lost. They were never going to be shown; only one of the
            # two drawings knew it.
            # MARKED, NOT DROPPED.
            #
            # Which groupings are worth drawing is a judgement, and a
            # judgement a reader cannot inspect is one they cannot
            # disagree with. Dropping them here also meant two drawings
            # of one folder that could not be compared - and the test
            # used the ROUND's copy of the flag, which does not carry it,
            # so on that path it quietly matched nothing at all.
            #
            # The rows are asked instead, every row is sent, and the page
            # decides. It hides them by default and has a button to show
            # them.
            "plan": {"nodes": [_marked(_with_last_name(one, known), hidden, leisure)
                               for one in (plan.get("nodes") or [])],
                     "edges": list(plan.get("edges") or [])},
            "layers": [_marked(trim(one), hidden, leisure)
                       for one in (held.get("layers") or [])],
            # NO TITLES. Measured, not guessed: the titles of 767 states
            # are 85KB of the 190KB these rows cost, and nothing in the
            # drawing reads one - every row is a summarised task now, and
            # a state is only counted (how many of a piece are finished)
            # and timed (how long a piece covers).
            "states": [{"id": one.get("id"),
                        "status": one.get("status", ""),
                        "starts_at": one.get("starts_at", ""),
                        "ends_at": one.get("ends_at", "")}
                       for one in (held.get("states") or [])],
            # TWO DIFFERENT THINGS, and they were one word.
            #
            # `running` is read from the last report: it says a round is
            # open, which stays true for an unfinished round long after
            # the visit that started it has ended. `at_work` says a
            # heartbeat is executing AT THIS MOMENT. Between visits the
            # first is true and the second is false, which is exactly
            # when nothing pulses - and a panel claiming to be running
            # with nothing moving on it reads as a panel that has broken.
            "running": bool(held.get("running")),
            # NOT THE THREAD. The loop thread is alive from the moment
            # the agent starts to the moment it stops, so asking it gives
            # "yes" at every instant and says nothing. What matters is
            # whether a VISIT is executing, which is what the round's own
            # progress records between its first step and its last.
            "at_work": bool((self.progress() or {}).get("running")),
            # BOTH WAYS OUT, every time. The other return is the one the
            # page usually gets, and a field added here alone is a field
            # the page never sees.
            "work": self._how_far_through(),
            "finished_at": held.get("finished_at", ""),
            "round": self._round_record(),
            "due": self.due_now(),
            "waiting": list(held.get("waiting") or []),
            "as_of": self._as_of(held),
            "summarising": str(held.get("summarising") or ""),
            "summarising_all": [str(one) for one in
                                held.get("summarising_all") or []],
            "at_once": dict(held.get("at_once") or {}),
            # THE PROJECTS, read from where they are kept rather than from
            # the round's report - which was the one way out that did not
            # carry them, and the one the page usually got.
            "projects": self._projects_now(),
        }

    def _projects_now(self) -> List[Dict[str, Any]]:
        """The projects as the page draws them, from what is kept."""
        try:
            kept = Kept(self.db, LIVE)
            tasks = {task.id: task for task in kept.live() + kept.archived()}
            return self._projects_drawn(
                [one for one in _Projects(self.db, LIVE).all() if one.tasks],
                tasks)
        except Exception:        # pragma: no cover - a read, not a step
            log.warning("could not read the projects", exc_info=True)
            return []

    def progress(self) -> Dict[str, Any]:
        return dict(self._progress)

    def status(self) -> Dict[str, Any]:
        now = time.time()
        enabled = bool(self.config.get("state_heartbeat_enabled", True))
        people = getattr(self, "connections_round", None)
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "enabled": enabled,
            # The people step of the round: its bar in the panel.
            "connections": dict(people.progress) if people is not None else None,
            "interval_seconds": self._interval(),
            # WHY IT WILL OR WILL NOT RUN, not only how often it looks.
            # The interval is how often the gate is consulted, which is a
            # different number from how often a round happens - a reader
            # shown only the first would be told rounds are hourly when
            # they are six-hourly.
            "due": self.due_now(),
            "every_hours": self._every_hours(),
            "round": self._round_record(),
            "stands_out": self._stands_out(),
            "min_nodes": self._min_nodes(),
            # ROUNDS, NOT CHECKS.
            #
            # These two read the loop's own clock, which is stamped on
            # every LOOK - and the loop only looks: once a minute it asks
            # whether a round is open or its hours are up, and nearly
            # always the answer is no. So the status bar said "last ran
            # 30 seconds ago, next in 30 seconds" about a thing that runs
            # every six hours. The other heartbeats run every time they
            # wake and can use the loop clock; this one cannot.
            "last_run_seconds_ago": self._seconds_since_the_last_round(now),
            "next_run_in_seconds": (
                None if not enabled else self._seconds_to_the_next_round(now)),
            # How often it looks, for anyone who wants that instead.
            "checks_every_seconds": self._looks_every(),
            "last_check_seconds_ago": (None if self._last_run_at is None
                                       else max(0.0, now - self._last_run_at)),
            "last_report": self.last_report,
        }

    def _seconds_since_the_last_round(self, now: float) -> Optional[float]:
        """Since the last round stopped working - the moment the hours run
        from - or None if there has never been one."""
        stopped = str(self._round_record().get("completed_at") or "")
        if not stopped:
            return None
        try:
            when = datetime.fromisoformat(stopped)
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, now - when.timestamp())

    def _seconds_to_the_next_round(self, now: float) -> Optional[float]:
        """When the next round actually starts.

        Now, if one is open with something left to try - it is carried on
        within seconds. Otherwise the hours from when the last one
        stopped, which is when the gate will say yes.
        """
        if bool((self.progress() or {}).get("running")):
            return 0.0
        # Held up by an unreachable model: the next try is the retry.
        said = str(self.config.get("state_round_blocked_at", "") or "")
        if said and self._still_blocked():
            try:
                when = datetime.fromisoformat(said)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                return max(0.0, when.timestamp()
                           + self.RETRY_UNREACHABLE_AFTER - now)
            except ValueError:
                pass
        record = self._round_record()
        if (not record.get("done") and record.get("started_at")
                and not record.get("exhausted")):
            return 0.0
        since = self._seconds_since_the_last_round(now)
        if since is None:
            return 0.0
        return max(0.0, self._every_hours() * 3600 - since)


def _as_json(text: str) -> Optional[Dict[str, Any]]:
    """The JSON object in a reply, whatever it is wrapped in."""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        found = json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        return None
    return found if isinstance(found, dict) else None


__all__ = ["StateHeartbeat", "similarity", "SUMMARY_PROMPT"]
