"""Each piece of work as a tree of cards, with its attempts inside them.

A unit is a project - all of its tasks, in one tree - or a task in no
project. Its tree: the ROOT is the work's goal; under it the DELIVERABLES
the user would recognise; under those the PARTS, each one output with one
check. A card says what is true when it is done, how that is judged and why
the card above needs it. A STAGE is an attempt inside a card - its input,
one decision and who made it, its output, the verdict - so retries and
dropped routes stay in the card they were attempts at.

After a state round, every unit with new states is extended: the model is
shown the unit's TREE (every card, with the last attempts of the cards
still going), the states that are new since last time, and the states left
open last time; it answers with operations that only ADD - new cards, new
stages in them, the edges from stages to what they depend on, cards done or
set aside, and the states not yet in a closed stage. Nothing already
written is rewritten, except a correction it gives a reason for.

The same call builds a graph from nothing: a unit's history is put through
it a chunk at a time, oldest first, from an empty frontier. One path for
both, so whatever goes wrong in one shows up in the other.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .store import model_task
from ...common.languages import in_english

#: WHICH LANGUAGE A TASK'S STAGES ARE WRITTEN IN: English - every stage and
#: card, all of their text, the natures and each stage's keywords - whatever
#: the states, records or files are in. They are the agent's own: not shown
#: to the user (2026-10-18), read by the agents, matched and searched - by
#: their words and by a model of meaning that reads English - which works in
#: one language. The user's own words, quoted, stay as they wrote them.
STAGE_LANGUAGE = in_english(
    "every stage and card, all of their text, every NATURE and each stage's KEYWORDS - "
    "they are matched and searched in one language; the user's own words, where you "
    "quote them, stay exactly as they wrote them")
from .store import (CARD_STATUSES, CHECKED_BY, EDGE_KINDS, MEDIA,
                    STAGE_KINDS, ExperienceStore, LIVE, new_id, now)

log = logging.getLogger(__name__)

#: How many new states one call reads. Enough for a few loops of work to be
#: seen whole; few enough that a call's answer is about them.
STATES_PER_CALL = 40

#: How many calls a round may spend extending graphs. The rest wait for the
#: next round, or for "build now".
CALLS_PER_ROUND = 4

#: How many cards are shown with their attempts: the ones being worked on
#: now. Every other card is one line, and its attempts are an open_card away
#: - so what a call is shown stays about the same size however long the
#: work runs.
ACTIVE_CARDS = 8

#: The rounds of opening cards a call may spend before it answers.
CARD_ROUNDS = 4

#: The batch each piece of work is read in after an answer was cut off,
#: by (scope, unit id) - remembered while the agent runs, so the next round
#: starts where the last one found a size that fits.
BATCH_SIZE: Dict[Tuple[str, str], int] = {}

#: The fewest states a cut-off answer is asked again with. Halved from
#: STATES_PER_CALL until then; cut off even here, it is an error.
SMALLEST_BATCH = 10

#: What a provider says when an answer stopped at its length limit, not
#: because it was finished.
CUT_OFF = {"max_tokens", "length", "incomplete", "max_output_tokens"}

#: Asked of an answer from a folder that was cut off: the same reading,
#: fewer stages.
FEWER_STAGES = ("YOUR ANSWER WAS CUT OFF - it was too long to finish. Answer "
                "again, with no more reading: AT MOST SIX stages, of the "
                "earliest part of the period, and covered_until where they "
                "reach. Only the JSON object.")

STAGE_PROMPT = """TURN THIS PIECE OF THE USER'S WORK INTO THE STAGES OF ITS LOOP.

Work goes round a loop: a GOAL is set, a PLAN is made, the plan is
carried out (EXECUTION), a result is EVALUATED, the plan is ADJUSTED, and
round again - until a deliverable is accepted and handed over (DELIVERY).
You are extending a record of that loop for one piece of work: a project,
or a task in no project. It is a TREE OF CARDS - the pieces of the result -
and inside each card, its STAGES: the attempts at it, joined by what depends
on what.

You are shown:

  - THE WORK: what it is for.
  - THE CARDS: the work's tree so far - EVERY card, one line each, with
    its id. The cards being worked on now also show their last attempts;
    any other card's attempts, and the cards under it, are one open_card
    away. A branch that is all done or set aside is one line.
  - THE NEW STATES: what the user did since the record was last extended,
    oldest first, each with its id. States marked "carried" were shown last
    time and were not yet part of a closed stage.
  - each state's MATERIALS: what its work was done with or on, linked
    and kept when the state was written - the user's own messages to their
    agents (exactly as they wrote them: the goal, the ask and the verdict
    are there), pictures of slides and figures, documents and emails read,
    files opened or saved, pages. Each has its id, its kind, its name, and
    the copies kept of it.

THIS RECORD IS FOR DOING THE WORK AGAIN. An agent will later plan and do a
piece of the user's work by reading what the user did here - so every
stage must say what an agent would need to do it the same way: what was
known, what was decided from it and by whom, and what came of it - and
where the things it used and made are. NEVER write how long it took, how
many exchanges a session reached, whether a session was alive or silent,
or what the user was doing between bursts: none of that helps anyone do
the work, and it is not the work.

THE LOGIC OF THE WORK, NOT THE RHYTHM OF THE DAY. A stage is about how the
work goes - what was decided, done, checked, sent back and changed - and
never about when or how much the user was at it: not bursts or sittings,
not "came back over several sittings", not "in the same sitting", not
breaks, not working late, not the agent carrying on while the user was
away. That is how the user spends their time, which is their personal
nature and is recorded elsewhere. Timing belongs in a stage only when it
is a decision in the work itself - "committed only once the full suite
passed" is a gate; "worked in bursts between meals" is not.

A STAGE IS A MODULE OF THINKING, BUILT ROUND ONE MAIN DECISION. Given some
INPUT, a DECISION was made, and an OUTPUT came of it. Several decisions are
several stages: an option tried and dropped is a stage of its own, and the
one that replaced it another, joined to it by an edge - so what was tried,
what did not work and what was done instead all show. A stage is usually
several states.

  - kind: goal, plan, execution, evaluation, adjustment or delivery. Each
    has its usual shape:
      goal        input: a need or a request    output: a goal, and what
                                                 would count as meeting it
      plan        input: a goal, constraints    output: a plan
      execution   input: a plan                 output: a deliverable
      evaluation  input: a deliverable, criteria output: a verdict, and
                                                 what to change
      adjustment  input: a verdict, a problem   output: a revised plan
      delivery    input: an accepted result     output: what was handed over
  - medium: what the work produces - email, message, code, document,
    figure, slides, data, form, meeting, setup, or other.
  - summary: one or two sentences: the decision, why, and what came of it.
  - input: WHAT THE DECISION WAS BASED ON - the information in hand when it
    was made: the user's request and words, the evidence seen (a result, an
    error, a referee's comment, a measurement), the constraints, and the
    outputs of earlier stages. Not the data itself: what in it mattered for
    the decision. Each says where it came from - "from":
      "user"      what the user said, asked or showed;
      "stage"     an earlier stage's output - its id or ref in "stage" (the
                  edge to it is made from this);
      "material"  a material shown;
      "workspace" a file, commit, page or conversation in the folder.
    EVERY INPUT POINTS AT WHERE IT CAN BE OPENED - its reference, by its
    id in "references" ("ref": "r2") - whoever it came from. The user's
    words are in a message they sent or a conversation; a page they read is
    a material or a URL; a result is in a file, a commit or the reply that
    reported it. Only an earlier stage's output may name the stage alone.
    Leave "ref" empty only when nothing of it was kept anywhere.
  - decision: THE ONE MAIN DECISION, made from that input:
      - about: what was being decided;
      - options: what was on the table - the user's ideas, the agent's
        proposals, the alternatives raised;
      - chose: what was chosen;
      - by: WHO ACTUALLY MADE THE CALL - "user" when they chose, asked for
        it, approved it or overruled the agent; "agent" when the agent chose
        by itself and the user never weighed in. Both count; never call the
        agent's choice the user's;
      - why: which input made this the choice - the user's own words where
        they gave them;
      - rejected: what was turned down or put off, and why.
  - output: WHAT CAME OF THE DECISION:
      - what: the output in a sentence - a goal, a plan, a design, a
        deliverable, a verdict, a revised plan;
      - specification: what the output had to satisfy - its requirements
        and acceptance criteria - ONLY WHERE THE USER SET THEM, each with
        its "source": the user's words ("the user said: '...'") or what the
        user checked ("the user checked: ..."). A requirement the agent
        assumed is not one: with no such source, leave the list empty;
      - design: how the output is built - its approach and structure, its
        parts and how they connect to each other and to the rest of the
        work. The idea and the structure, not every command or edit;
      - rationale: WHY IT IS DESIGNED THIS WAY - what the design is meant
        to achieve or avoid. Where the user said why, give their reason in
        their own words and "from": "user"; otherwise infer it from the
        input and the work, and say "from": "inferred";
      - example: for a stage that produced something, THE THING PRODUCED -
        often the most important reference of all, because it is what the
        work looks like when it is done:
          - type: code, document, slides, figure, email, message, data,
            form, or other;
          - excerpt: a short description of it;
          - references: the ids, in "references", of what IS it - the
            file as it was saved, the agent's answer that was the result,
            the email sent, the picture of the finished slide, and for code
            the COMMIT (its hash and the repository's path - the states and
            the agent's replies name them: "committed as c4e7a19").
        It must name at least one, so it can be looked up.
  - evaluation: for an evaluation stage, the verdict (accepted, rejected,
    partly), the criteria the user applied, and THE USER'S OWN WORDS,
    quoted exactly as the states quote them. These are how the user judges
    work.
  - references: THE STAGE'S REFERENCES, ALL OF THEM, in one list: where
    each input came from, what was read, and what was produced. Each has a
    short id - "r1", "r2", ... - and is named by its id in "material", or by
    its exact "location" (a full path, a URL, a commit hash and its
    repository, a conversation's session and time), with what in it to look
    at. Never make a location up.

POINT AT THE REFERENCES. Anywhere in the text - the summary, an input, the
decision's why, the output's specification, design or rationale - a claim
that rests on a reference points at it by its id in square brackets: "the
referee's comment that the instrument is weak [r1]", "the robustness table
is rerun [r3]". A reader goes from the sentence to the thing itself.

HIGH LEVEL, WITH EXACT REFERENCES. The input, decision and output are the
thinking, said plainly; the detail - printed error text, whole command
lines, every file changed, exact counts - is left to the references, named
exactly, with what in each to look at.

AN INVENTED EXAMPLE, from another kind of work - it shows the shape and
the level to write at; take nothing from its content. A stage of revising a
paper after a referee report:

  {"kind": "adjustment", "medium": "document",
   "summary": "The user decided to answer the second referee's doubt about
               the instrument [r1] with a stronger first stage rather than
               an argument, and chose the added instrument that has an
               economic story. The main estimate, the robustness table and
               the reply letter now rest on it [r2].",
   "input": [{"what": "the second referee's comment that the instrument is
                       weak [r1]", "from": "material", "ref": "r1"},
             {"what": "the first-stage strength with each candidate
                       instrument, from the agent's runs",
              "from": "stage", "stage": "n1"}],
   "decision": {"about": "which instrument to add",
                "options": ["the candidate with the strongest first stage",
                            "the candidate with a clear economic story"],
                "chose": "the candidate with a clear economic story",
                "by": "user",
                "why": "the referee's doubt was about whether the instrument
                        is excluded, so the user wanted one they could
                        defend: 'a referee will ask why it is excluded, and
                        I need an answer'",
                "rejected": "the strongest candidate, which had no story"},
   "output": {"what": "a rewritten identification section built on the
                       added instrument",
              "specification": [{"requirement": "every claim of the reply
                                 to the referee is backed by a table",
                                 "source": "the user said: 'nothing in the
                                 letter without a table behind it'"}],
              "design": "the identification section [r2] states the
                         exclusion argument first and then the first stage;
                         the robustness table is rerun with the added
                         instrument [r3], and the reply letter cites both",
              "rationale": {"why": "the referee questioned the exclusion, so
                                    the section leads with the argument the
                                    referee doubted before any numbers",
                            "from": "inferred"},
              "example": {"type": "document",
                          "excerpt": "the new identification section",
                          "references": ["r2", "r3"]}},
   "references": [{"id": "r1", "location": "D:/papers/trade/referee2.pdf",
                   "what": "the weak-instrument comment"},
                  {"id": "r2", "location": "D:/papers/trade/main.tex",
                   "what": "the rewritten identification section"},
                  {"id": "r3", "location": "D:/papers/trade @ 4b1e9c2",
                   "what": "the robustness table rerun with the new instrument"}]}

The agent's earlier runs of the candidates would be a stage of its own -
input: the referee's comment; decision (by the agent): which candidates to
try; output: their first-stage strengths - and this stage's input points
at it. Both are attempts inside the card for the identification section, a
deliverable under the root "the paper accepted at the journal"; the reply
letter is another deliverable, and the robustness table a part of the
identification section.

ONLY CLOSED STAGES. A stage is closed when what came after it has begun -
its result was evaluated, or the next step started. The states at the end
that are still in the middle of something go in "still_open"; you will be
shown them again next time, with what follows them.

THE WORK IS A TREE OF CARDS, AND EVERY STAGE IS AN ATTEMPT INSIDE ONE.

  - THE ROOT is the work's goal. It is shown with its id.
  - Under the root, DELIVERABLES: the pieces of the result the user would
    recognise and name - a section of the paper, a feature, the reply to a
    referee, the set of figures.
  - Under a deliverable, PARTS: each ONE output with ONE check - the
    robustness table, the parser's error handling, the opening paragraph
    of the letter. A part has parts of its own only when it truly breaks
    down further.

A card says:
  - title: a few words naming it.
  - type: what it is - email, message, code, document, figure, slides,
    data, form, meeting, setup, or other.
  - statement: WHAT IS TRUE WHEN IT IS DONE, in a sentence or two - the
    result, not the activity: "the identification section argues the
    instrument's exclusion before any number", not "work on the
    identification section".
  - specification: what the user required of it, ONLY with a source - the
    same rule as an output's.
  - checked_by: how it is judged - "by": "test" when something that runs
    decides it (the tests pass, it builds, the number matches), "user" when
    the user looks and decides, "agent" when an agent judged it and the user
    never weighed in; "what": what is looked at.
  - why: what the card above needs it for - the user's words with "from":
    "user" where they gave them, otherwise "from": "inferred".
  - parent: the card it is a piece of - an id shown, a new card's ref, or
    "root".
  - references: where the evidence for it is, ids "r1", "r2", ... - the
    "why" points at them.

A CARD IS FOUND IN THE WORK, NEVER INVENTED. Make one only when the states
or the folder show that piece of the result being worked on, and put it
under the card it actually served. Work that never broke down is a root
with its attempts and no cards under it - a normal answer.

USE THE CARDS THAT EXIST. Another attempt at something already carded goes
in that card: a second draft of the same section is another stage in the
section's card, not a new card. When a card's attempts are not shown and
you need them - to tell whether new work is another attempt at it, or to
point an edge at one of its stages - open it with open_card, by its id. A route that was dropped keeps its card,
set aside, with the card that replaced it.

EVERY STAGE NAMES ITS CARD ("card": an id shown, a new card's ref, or
"root"). What each kind of stage is, inside its card:
      goal        what the card must be - set, or changed
      plan        how the card will be made or broken down: a plan that
                  splits it is the decision, and the pieces are the cards
                  under it
      execution   making the card's output
      evaluation  judging it
      adjustment  changing the plan after a verdict
      delivery    the card accepted and handed over
The stages inside a card are its history - tried, judged, changed, tried
again - each with its own decision.

CARDS CHANGE in "cards_changed": "done" once the card's result was accepted
or handed over; "set_aside" when it was dropped, with "replaced_by" the
card that took its place if one did; and "why". A card with attempts under
way is working without being told.

THE ROOT'S OWN WORDS. When the states show what the whole work is for more
exactly than THE WORK says, give it in "root": its statement, and the
user's specification for the whole result, with sources.

EACH STAGE STANDS ON ITS OWN. It is read alone, long after, by an agent
that has never seen the others or the work itself, so every field must make
sense by itself:

  - NEVER NAME ANOTHER STAGE OR CARD BY ITS ID OR REF - not "n8", not
    "c2", not "stage_..." - in any text. Say what it is: not "the reminder
    template in n8 relies on it" but "the reminder template, added in the
    next step, relies on the check_status task". Ids and refs go in
    "stage", "card", "parent" and "edges" only.
  - NO LABEL FROM THE WORK ITSELF WITHOUT THE REAL THING. The work's own
    numbering and shorthand - a README's "loop ④", a paper's "Table 3", a
    plan's "step 2", a ticket's number, a variable's name - mean nothing to
    a reader without the work open. Write what it is, every time it would
    otherwise be unclear: not "this coupled loop ④ to loop ②" but "this
    made the scheduler (the README's loop ④, Action) depend on the data
    heartbeat (loop ②, Knowledge)"; not "rerun Table 3" but "rerun the
    robustness table (Table 3)".
  - NO PRONOUNS OR SHORTHAND FOR WHAT IS NOT IN THE SAME SENTENCE: name the
    thing - "the check_status task", not "it" or "this"; "the README's
    five-loop table", not "the table".

EDGES say what each new stage depends on - the stage it continues, the
deliverable it evaluates, the plan it revises. Every input that is an
earlier stage's output is one. Point only at stages shown to you (by id) or
at new stages in this answer (by their "ref"), and only at ones that came
before it.

  - continues: the next step of the same plan.
  - produces: the deliverable this execution made.
  - evaluates: the result this evaluation judged.
  - revises: the plan this adjustment changes.
  - depends_on: anything else it could not have happened without.

WHAT EACH STAGE SHOWS ABOUT THE USER'S WORK - its "natures", written with
it: how the user evaluates a result, and what output they want, that an
agent would need to do their work the way they would.

{nature_guide}

Each one rests on the stage it is written with: "evidence" says what in
the stage shows it - quote the user's own words where the stage has them -
and it is what the stage is evidence OF, not a retelling of it. A stage
with an evaluation, a specification or a decision of the user's almost
always shows one; a stage may show none, and an empty list says so.

NOT EVERY STATE IS WORK. States about something else - a break, another
task, the agent watching the user - belong to no stage: list them in
"unrelated".


Return ONLY this JSON object:
{
  "root": {"statement": "only when the states say it better than THE WORK",
           "specification": [{"requirement": "...", "source": "the user said: '...'"}]},
  "cards": [
    {"ref": "c1", "parent": "root, a card id shown, or a new card's ref",
     "title": "...", "type": "code|document|slides|figure|email|message|data|form|meeting|setup|other",
     "statement": "what is true when it is done",
     "specification": [{"requirement": "...", "source": "the user said: '...' | the user checked: ..."}],
     "checked_by": {"by": "test|user|agent", "what": "what is looked at"},
     "why": {"why": "what the card above needs it for [r1]", "from": "user|inferred"},
     "references": [{"id": "r1", "material": "...", "location": "...", "what": "..."}]}
  ],
  "new_stages": [
    {"ref": "n1", "kind": "evaluation", "medium": "code",
     "card": "root, a card id shown, or a new card's ref",
     "summary": "...",
     "keywords": ["6 to 15 words or short phrases this stage would be looked for by - what it was about, what was decided, the tools, files and terms"],
     "input": [{"what": "what mattered for the decision - pointing at its reference [r1]",
                "from": "user|stage|material|workspace",
                "stage": "for a stage's output: its id or ref",
                "ref": "the id of its reference in references"}],
     "decision": {"about": "what was being decided",
                  "options": ["what was on the table"],
                  "chose": "what was chosen", "by": "user|agent",
                  "why": "which input made it the choice - the user's own words where given",
                  "rejected": "what was turned down or put off, and why"},
     "output": {"what": "...",
                "specification": [{"requirement": "...",
                                   "source": "the user said: '...' | the user checked: ..."}],
                "design": "...",
                "rationale": {"why": "why it is designed this way",
                              "from": "user|inferred"},
                "example": {"type": "code|document|slides|figure|email|message|data|form|other",
                            "excerpt": "what was produced",
                            "references": ["the ids, in references, of what is it"]}},
     "evaluation": {"verdict": "rejected", "criteria": ["..."],
                    "words": ["the user's own words, quoted"]},
     "references": [{"id": "r1",
                     "material": "a material's id, as shown",
                     "location": "only for what has no material: a path, a URL, a hash and repository, a session",
                     "what": "what in it to look at"}],
     "states": ["the ids of the states it is made of"],
     "edges": [{"kind": "evaluates", "to": "a stage id, or a ref"}],
     "natures": [{"same_as": "a KNOWN NATURE's id - this trait again",
                  "example": {"said": "the user's own words, quoted exactly",
                              "what": "a short label of the case, one line",
                              "references": ["every reference id of this stage it touched: r1, r3, ..."]}},
                 {"aspect": "evaluation|output",
                  "title": "ONE sentence, the user as its subject",
                  "content": "a short paragraph: what it is, how it shows, and what it means for doing their work",
                  "confidence": 1-7,
                  "evidence": ["what in the stage shows it"],
                  "example": {"said": "the user's own words, quoted exactly",
                              "what": "a short label of the case, one line",
                              "references": ["every reference id of this stage it touched: r1, r3, ..."]}}]}
  ],
  "cards_changed": [{"card": "a card id or ref", "status": "done|set_aside",
                     "replaced_by": "a card id or ref, if one took its place",
                     "why": "..."}],
  "corrections": [{"stage": "...", "summary": "...", "why": "..."}],
  "still_open": ["state ids at the end, not yet in a closed stage"],
  "unrelated": ["state ids that are not part of this work"]
}"""

WORKSPACE_PROMPT = """

THE PROJECT'S WORKSPACE. This piece of work has a folder, and you can read
it with tools - list_dir, read_file, find_files, git_log, git_show and
conversation - before you answer. What you are told about what changed is a
list; the work itself is in the folder. READ BEFORE YOU WRITE:

  - the conversations hold the user's own words - what they asked for,
    what they checked, what they sent back and why, what they decided;
  - the history holds what actually changed, commit by commit;
  - the files hold what was produced - drafts, final versions, figures.

Read as much as you need to say concretely what was planned, done,
checked, decided and changed - following what matters, not everything.

You are writing the stages of the work done in the period shown. THE FIRST
TIME a folder is read it is its whole history: write the whole arc - how
the work was first planned and set up, how the plan changed and why, each
deliverable with its attempts in order, and what was delivered.

Each stage written here says WHEN it happened: "began" and "ended" as ISO
dates, from the commits, files and conversation turns it rests on. Its
"states" are the states shown that it rests on, if any - it may have none.
References name files by their path in the folder, and commits by hash.

A LONG HISTORY IS READ IN PARTS. Write AT MOST TWELVE stages in one
answer, of the earliest part of the period you have read properly, and
return "covered_until": the ISO date your stages reach. The rest is read
next time, from that date - with what you wrote as its frontier. Leave it
out only when you have covered the whole period. Read about as much as
those twelve stages need, not the whole history at once.

For a folder the user added - one with no states - also return "project":
{"title": "...", "goal": "...", "status": "..."}, one line each: what the
project is, what it is for, and where it stands now.

These fields come in addition to the ones above:
{"new_stages": [{..., "began": "ISO date", "ended": "ISO date"}],
 "covered_until": "ISO date, only when the period is not all covered",
 "project": {"title": "...", "goal": "...", "status": "..."}}"""

from .stage_natures import NATURE_GUIDE as _NATURE_GUIDE  # noqa: E402

STAGE_PROMPT = STAGE_PROMPT.replace("{nature_guide}", _NATURE_GUIDE)

JOIN_PROMPT = """CONNECT A TASK'S STAGES TO THE PROJECT IT HAS JOINED.

A task has just been put in a project. Both already have records of their
work as stages - attempts inside the cards of their work. Say which stages
of the TASK depend on, continue or revise which stages of the PROJECT -
where one could not have happened without the other, or carries it on.
Most pairs have nothing between them; an empty list is a normal answer.

Point from the later stage to the earlier one, using the stage ids shown.

Return ONLY this JSON object:
{"edges": [{"from": "stage id", "to": "stage id",
            "kind": "continues|depends_on|revises", "why": "one sentence"}]}"""


def _open_card_tool():
    from ...models.base import ToolSpec
    return ToolSpec(
        "open_card",
        "Open cards of THIS work's tree by their ids: what each says, the "
        "cards under it, and every attempt at it in order - for a card whose "
        "attempts are not shown, or a branch shown as one line.",
        {"type": "object",
         "properties": {"ids": {"type": "array", "items": {"type": "string"},
                                "description": "card ids, as shown"}},
         "required": ["ids"]})


#: The one tool the writer has over its own tree: nothing outside it.
OPEN_CARD = _open_card_tool()


def _decision_of(value: Any) -> Dict[str, Any]:
    """The stage's one main decision, as kept: its subject, the options, the
    choice and who made it, why, and what was turned down."""
    if isinstance(value, list):         # an answer that gave a list: its first
        value = next((one for one in value if isinstance(one, dict)), {})
    if not isinstance(value, dict) or not str(value.get("about") or
                                              value.get("chose") or "").strip():
        return {}
    by = str(value.get("by") or "").strip().lower()
    return {
        "about": str(value.get("about") or "")[:400],
        "options": [str(o)[:300] for o in (value.get("options") or [])
                    if str(o).strip()][:8],
        "chose": str(value.get("chose") or "")[:500],
        "by": by if by in ("user", "agent") else "",
        "why": str(value.get("why") or "")[:1200],
        "rejected": str(value.get("rejected") or "")[:500]}


def _example_of(example: Dict[str, Any],
                references: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What a stage produced, its references named by id - "r2" - turned into
    the references themselves: a material among its materials, anything
    else as a location to pin down, each keeping its id so the text can
    point at it."""
    by_id = {one["id"]: one for one in references if one.get("id")}
    out = dict(example)
    materials = list(out.get("materials") or [])
    pinned = []
    for one in out.get("references") or []:
        if isinstance(one, dict):
            pinned.append(one)
            continue
        found = by_id.get(str(one))
        if found is None:
            continue
        if found.get("material") and not found.get("location"):
            materials.append(found["material"])
        else:
            pinned.append({"id": found["id"], "location": found["location"],
                           "what": found["what"]})
    out["materials"], out["references"] = materials, pinned
    return out


def _rationale_of(value: Any) -> Dict[str, str]:
    """Why the output is designed as it is - the user's reason where they
    gave one, otherwise inferred, and said which."""
    if isinstance(value, str):
        value = {"why": value}
    if not isinstance(value, dict) or not str(value.get("why") or "").strip():
        return {}
    source = str(value.get("from") or "").strip().lower()
    return {"why": str(value["why"])[:1000],
            "from": "user" if source == "user" else "inferred"}


def _references_of(value: Any) -> List[Dict[str, Any]]:
    out = []
    for one in value or []:
        if not isinstance(one, dict):
            continue
        material = str(one.get("material") or one.get("event") or "").strip()
        location = str(one.get("location") or "").strip()
        if not material and not location:
            continue
        out.append({
            "id": str(one.get("id") or "")[:12],
            "material": material[:60],
            "kind": str(one.get("kind") or "other")[:20],
            "location": location[:600],
            "what": str(one.get("what") or "")[:400]})
    return out[:30]


def _specification_of(value: Any) -> List[Dict[str, str]]:
    """Requirements the user set, each with where they set it - one with no
    source is the agent's assumption, and is not kept."""
    out = []
    for one in value or []:
        if isinstance(one, str):
            one = {"requirement": one}
        if not isinstance(one, dict):
            continue
        requirement = str(one.get("requirement") or "").strip()
        source = str(one.get("source") or "").strip()
        if requirement and source:
            out.append({"requirement": requirement[:500], "source": source[:500]})
    return out[:10]


def _card_of(entry: Dict[str, Any]) -> Dict[str, Any]:
    """What a card says, as kept: its title and type, what is true when it
    is done, the user's specification, how it is judged, and why the card
    above needs it."""
    kind = str(entry.get("type") or "").strip().lower()
    checked = entry.get("checked_by")
    if isinstance(checked, str):
        checked = {"by": checked}
    checked = checked if isinstance(checked, dict) else {}
    by = str(checked.get("by") or "").strip().lower()
    return {"title": str(entry.get("title") or "")[:300],
            "type": kind if kind in MEDIA else "other",
            "statement": str(entry.get("statement") or "")[:1500],
            "specification": _specification_of(entry.get("specification")),
            "checked_by": {"by": by if by in CHECKED_BY else "",
                           "what": str(checked.get("what") or "")[:600]},
            "why": _rationale_of(entry.get("why")),
            "refs": _references_of(entry.get("references"))}


def depth_of(card: Dict[str, Any], cards: Dict[str, Dict[str, Any]]) -> int:
    """How far below the root a card is: 0 the root, 1 a deliverable, 2 and
    more a part."""
    depth, seen, here = 0, set(), card
    while here.get("parent_id") and here["parent_id"] in cards \
            and here["id"] not in seen:
        seen.add(here["id"])
        here = cards[here["parent_id"]]
        depth += 1
    return depth


#: What each depth of the tree is called.
def level_of(depth: int) -> str:
    return "root" if depth == 0 else "deliverable" if depth == 1 else "part"


def _work_of(entry: Dict[str, Any]) -> Dict[str, Any]:
    """What a stage says about the work, as kept: its input, its one main
    decision, its output - what, the specification the user set, its
    design - and what was read for it."""
    inputs = []
    for one in entry.get("input") or []:
        if isinstance(one, str):
            one = {"what": one}
        if not isinstance(one, dict) or not str(one.get("what") or "").strip():
            continue
        source = str(one.get("from") or "").strip().lower()
        inputs.append({
            "what": str(one.get("what") or "")[:600],
            "from": source if source in ("user", "stage", "material",
                                         "workspace") else "",
            "stage": str(one.get("stage") or "")[:60],
            "ref": str(one.get("ref") or "")[:12],
            "material": str(one.get("material") or "")[:60],
            "location": str(one.get("location") or "")[:600]})
    output = entry.get("output") if isinstance(entry.get("output"), dict) else {}
    # Only what the user set: a requirement with no source is the agent's
    # assumption, not the user's specification.
    specification = _specification_of(output.get("specification"))
    return {"input": inputs[:12],
            "decision": _decision_of(entry.get("decision")
                                     or entry.get("decisions")),
            "output": {"what": str(output.get("what") or "")[:800],
                       "specification": specification[:10],
                       "design": str(output.get("design") or "")[:1500],
                       "rationale": _rationale_of(output.get("rationale"))},
            "references": _references_of(entry.get("references"))}


#: A path as a stage writes it: a whole one ("C:\\x\\y.py"), or a
#: relative one with at least one separator ("personalized_agent\\x.py").
_PATHISH = re.compile(r"[A-Za-z]:[\\/][^\s,;'\"()]+|"
                      r"(?<![\w:])[\w.\-]+(?:[\\/][\w.\-]+)+")


def _split_paths(references: List[Dict[str, Any]],
                 repos: Sequence[Path]) -> List[Dict[str, Any]]:
    """References whose location names local files the way people write
    them - relative to a repository ("personalized_agent\\x.py in
    Desktop\\personalized_agent_release"), or two at once ("a.py and
    b.py") - each made a whole path that exists, one reference per file.
    What names nothing local is left as it is."""
    from .references import access_of
    home = Path.home()
    out: List[Dict[str, Any]] = []
    for one in references:
        location = str(one.get("location") or "")
        if one.get("copies") or "://" in location or location.startswith(
                ("event_", "state_")) or Path(location).exists():
            out.append(one)
            continue
        # A name in the project's own folder - "plan.md", "src/app.js".
        inside = [base / location.strip() for base in repos
                  if location.strip() and not Path(location.strip()).is_absolute()]
        here = next((one for one in inside if one.exists()), None)
        if here is not None:
            made = dict(one, location=str(here),
                        kind="file" if here.is_file() else "folder")
            made["access"] = access_of(made)
            out.append(made)
            continue
        tokens = _PATHISH.findall(location)
        bases = list(repos) + [home] + [home / token for token in tokens
                                        if (home / token).is_dir()]
        found: List[Path] = []
        for token in tokens:
            token = token.rstrip(".")
            candidates = [Path(token)] if Path(token).is_absolute() else \
                [base / token for base in bases]
            for candidate in candidates:
                try:
                    if candidate.exists() and candidate not in bases \
                            and candidate not in found:
                        found.append(candidate)
                        break
                except OSError:
                    continue
        # A bare name beside the first - "service.py and patterns.py" - is
        # the file next to it.
        if found:
            beside = found[0].parent if found[0].is_file() else found[0]
            for name in _BARE.findall(location):
                candidate = beside / name
                if candidate.exists() and candidate not in found:
                    found.append(candidate)
        if not found:
            out.append(one)
            continue
        for path in found[:6]:
            made = dict(one, location=str(path),
                        kind="file" if path.is_file() else "folder")
            made["access"] = access_of(made)
            out.append(made)
    return out


#: A file's bare name: "patterns.py", "app.js".
_BARE = re.compile(r"(?<![\\/\w.])[\w\-]+\.[A-Za-z0-9]{1,6}\b")


#: A commit hash as people write it: seven to forty hex digits, with at
#: least one letter and one digit - a plain number or word is not one.
_HASH = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b")


def _shrink_old_results(messages: List[Dict[str, Any]], keep: int) -> None:
    """Tool results more than `keep` rounds old cut to their start: every
    round is sent again in full, and a pass that kept everything it ever
    read grew past what one call can answer in time."""
    rounds = [one for one in messages if one.get("role") == "user"
              and isinstance(one.get("content"), list)]
    for message in rounds[:-keep]:
        for part in message["content"]:
            if part.get("type") == "tool_result" and \
                    len(str(part.get("content") or "")) > 1500:
                part["content"] = str(part["content"])[:1500] + \
                    " ... (read earlier; cut here to keep the reading small)"


def unit_task_ids(unit: Dict[str, Any]) -> List[str]:
    """The task ids a unit's stages are kept under: its tasks', or - for a
    folder the user added, which has no tasks of the listener's - its own."""
    ids = [task.id for task in unit.get("tasks") or []]
    return ids or [str(unit["id"])]


def _date_of(value: Any) -> str:
    """A date a stage gives itself, as an ISO time - or "" if it is not one."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.isoformat()


def _materials_said(materials: Sequence[Dict[str, Any]]) -> List[str]:
    """A state's materials as the stage writer reads them: id, kind, name,
    which copies there are, and - for the user's words - what they said."""
    out = []
    for one in materials[:12]:
        copies = ", ".join(sorted((one.get("copies") or {})))
        line = (f"material [{one['event_id']}] {one['kind']}: "
                f"{str(one.get('title') or one.get('location') or '')[:120]}"
                + (f" (kept: {copies})" if copies else ""))
        if one["kind"] == "message" and one.get("title"):
            text = _read_copy((one.get("copies") or {}).get("text"), 500)
            line += f"\n      “{text or one['title']}”"
        elif one["kind"] == "reply" and one.get("title"):
            text = _read_copy((one.get("copies") or {}).get("text"), 240)
            line += f"\n      agent: {text or one['title']}"
        out.append(line)
    if len(materials) > 12:
        out.append(f"... and {len(materials) - 12} more materials")
    return out


def _read_copy(path: Any, most: int) -> str:
    """The body of a kept text copy, after its header."""
    if not path:
        return ""
    try:
        text = Path(str(path)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    body = text.split("\n---\n", 1)[-1].strip()
    body = " ".join(body.split())
    return body if len(body) <= most else body[:most - 1] + "…"


def _with_copies(references: List[Dict[str, Any]],
                 materials: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each reference with the copies its material kept, its original, and
    whether that original can be opened. Nothing is searched: a reference is
    a material of the stage's states, or a location the states gave."""
    from .references import access_of
    out = []
    for reference in references:
        reference = dict(reference)
        found = materials.get(reference.get("material") or "")
        if found is not None:
            reference.update(kind=found["kind"], copies=found.get("copies") or {},
                             location=reference.get("location") or found["location"],
                             title=found.get("title") or "")
        elif reference.get("material"):
            # Named but not one of these states' materials: kept as said,
            # with nothing attached.
            reference["location"] = reference.get("location") or reference["material"]
        reference["access"] = access_of(reference)
        out.append(reference)
    return out


def _parsed(text: str) -> Optional[Dict[str, Any]]:
    """The answer's JSON object - or None when there is none to read, which
    is what an answer cut off part way looks like."""
    text = text or ""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text[text.find("{"):text.rfind("}") + 1]
    if not raw:
        return None
    try:
        out = json.loads(raw)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _cut_off(response: Any) -> bool:
    """An answer that stopped at its length limit, or that cannot be read -
    never applied, since what it left out would be marked as read."""
    if response is None:
        return False
    if str(getattr(response, "stop_reason", "") or "").lower() in CUT_OFF:
        return True
    return _parsed(getattr(response, "text", "") or "") is None


def _add_to_last_user(messages: List[Dict[str, Any]], text: str) -> None:
    """Say something more in the user's last turn - after its tool results,
    or after its text."""
    last = messages[-1] if messages else None
    if last is None or last.get("role") != "user":
        messages.append({"role": "user", "content": text})
    elif isinstance(last.get("content"), list):
        last["content"].append({"type": "text", "text": text})
    else:
        last["content"] = f"{last.get('content') or ''}\n\n{text}"


def _as_json(text: str) -> Dict[str, Any]:
    text = text or ""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text[text.find("{"):text.rfind("}") + 1]
    try:
        out = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _writer(response: Any) -> Dict[str, str]:
    return {"model": str(getattr(response, "model", "") or ""),
            "provider": str(getattr(response, "provider", "") or ""),
            "at": now()}


def _when(state: Any) -> str:
    local = ((getattr(state, "metadata", None) or {}).get("local") or {})
    said = local.get("starts_at") or getattr(state, "starts_at", None)
    return str(said.isoformat() if hasattr(said, "isoformat") else said or "")


def _iso(value: Any) -> str:
    return str(value.isoformat() if hasattr(value, "isoformat") else value or "")



def task_name(task) -> str:
    """A task's name as it is now - and while it is being described again,
    the name it had, rather than its id. The tasks are named by the state
    heartbeat, and a task renamed there is renamed here: nothing here keeps
    a copy of the name."""
    said = getattr(task, "described", None) or {}
    was = said.get("was") or {}
    return str(said.get("title") or was.get("title") or getattr(task, "id", ""))

class StageWriter:
    """Extends the stage graph of every unit with new states."""

    def __init__(self, db: Any, states: Any, models: Any, scope: str = LIVE,
                 going: Optional[Callable[[], bool]] = None) -> None:
        self.store = ExperienceStore(db, scope)
        self.db = db
        self.states = states
        self.models = models
        self.scope = scope
        self.going = going or (lambda: True)
        #: Gives each reference its access, a local original and a snapshot
        #: of what was seen - set by the service, which knows the workspace.
        #: The states' materials - set by the service, which knows the
        #: workspace; a stage's references are taken from them.
        self.materials = None
        #: What its model calls cost, for the round's account of each part.
        self.used = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        #: The pass's cutoff: only states written by then are read. Set by
        #: the service from the pass it is carrying on; empty reads all.
        self.until = ""
        #: Where what a stage shows about the user is written, with it - set
        #: by the service. With none, the stages are read for it afterwards.
        self.library = None
        #: What stages are written in: English (STAGE_LANGUAGE) - the
        #: natures written with them too.
        self.language = "English"
        #: What finds the user's known natures closest to the work, so a
        #: trait seen again is added to its nature - and a task's project
        #: and title for each example. Set by the service.
        self.embedder = None
        self.names = None
        #: The natures written with the stages of the answer being applied:
        #: the known ones it was shown, which a `same_as` may name.
        self._natures = None

    # -- units ----------------------------------------------------------------
    def units(self, tasks: Sequence[Any], projects: Sequence[Any]
              ) -> List[Dict[str, Any]]:
        """Projects with their tasks, and every task in no project."""
        live = {task.id: task for task in tasks}
        out, placed = [], set()
        for project in projects:
            members = [live[one] for one in (project.tasks or []) if one in live]
            if not members:
                continue
            placed.update(task.id for task in members)
            english = (project.described or {}).get("in_english") or {}
            out.append({"id": project.id, "kind": "project",
                        "name": project.name, "why": project.why,
                        "status": (project.described or {}).get("status", ""),
                        # What it is searched by, in English (its copy, where
                        # the user reads another language).
                        "english": f"{english.get('name') or project.name} "
                                   f"{english.get('status') or project.why or ''}",
                        "tasks": members})
        for task in tasks:
            if task.id in placed:
                continue
            said = task.described or {}
            english = said.get("in_english") if isinstance(said.get("in_english"), dict) else {}
            out.append({"id": task.id, "kind": "task",
                        "name": task_name(task),
                        "why": said.get("goal", ""),
                        "status": said.get("status", ""),
                        "english": f"{english.get('title') or task_name(task)} "
                                   f"{english.get('goal') or said.get('goal') or ''}",
                        # A project's earlier names (section 34.5): renamed
                        # as it grew, and the ones it carries on.
                        "names_before": list(said.get("names_before") or []),
                        "tasks": [task]})
        return out

    # -- keeping up with tasks that merged, split, joined or left ----------------
    def follow_the_tasks(self, tasks: Sequence[Any], retired: Sequence[Any],
                         projects: Sequence[Any]) -> Dict[str, int]:
        """Stages follow their states; joins and leavings add or drop edges."""
        moved = 0
        live_ids = {task.id for task in tasks}
        # A task joined into another: its stages and cards go with it - to
        # the end of the chain, when the one it went into went on into
        # another (a project carried on by one that then came back as an
        # earlier one, section 34.5).
        went = {old.id: str((old.born_of or {}).get("joined_into") or "") for old in retired}
        for old in retired:
            into, seen = went[old.id], {old.id}
            while into and into not in live_ids and into in went and into not in seen:
                seen.add(into)
                into = went[into]
            if into not in live_ids:
                continue
            stages = self.store.stages([old.id])
            if stages:
                self.store.move_stages([s["id"] for s in stages], into)
                moved += len(stages)
            # Its cards too - its root among them, which becomes a
            # deliverable of the task it went into.
            self.store.move_cards(old.id, into)
            # And which of its states were already read: the one carrying it
            # on does not read them again (a project carried on, section
            # 34.5; one carried on by none keeps all of it, as history).
            gone, kept = self.store.mark(old.id), self.store.mark(into)
            if gone["states_seen"] or gone["open_tail"]:
                self.store.set_mark(into, kept["states_seen"] + gone["states_seen"],
                                    kept["open_tail"] + gone["open_tail"])
                self.store.set_mark(old.id, [], [])
        # A task split out of another: a stage goes with the task that holds
        # most of its states.
        member_of: Dict[str, str] = {}
        for task in tasks:
            for one in task.members:
                member_of.setdefault(one, task.id)
        for task in tasks:
            came_from = list((task.born_of or {}).get("from") or [])
            if (task.born_of or {}).get("kind") != "split from":
                continue
            for stage in self.store.stages(came_from):
                owners = [member_of.get(one) for one in stage["states"]]
                if owners and owners.count(task.id) > len(owners) / 2:
                    self.store.move_stages([stage["id"]], task.id)
                    moved += 1
        return {"moved": moved}

    def joins(self, projects: Sequence[Any]) -> List[Dict[str, Any]]:
        """Connect tasks newly in a project; undo the joins of tasks that left."""
        done = []
        in_project = {(project.id, one) for project in projects
                      for one in (project.tasks or [])}
        for row in self.db.query(
                "SELECT task_id FROM experience_marks WHERE scope = ? AND "
                "task_id LIKE 'join:%'", (self.scope,)):
            _join, project_id, task_id = row["task_id"].split(":", 2)
            if (project_id, task_id) not in in_project:
                # Every edge a join in this project made that touches the
                # task that left - whichever side's joining made it.
                dropped = self.store.drop_join_edges(
                    project_id, [s["id"] for s in self.store.stages([task_id])])
                self.db.execute("DELETE FROM experience_marks WHERE task_id = ? "
                                "AND scope = ?", (row["task_id"], self.scope))
                done.append({"left": task_id, "project": project_id,
                             "edges_dropped": dropped})
        for project in projects:
            for task_id in project.tasks or []:
                key = f"join:{project.id}:{task_id}"
                if self.db.query("SELECT 1 FROM experience_marks WHERE "
                                 "task_id = ? AND scope = ?", (key, self.scope)):
                    continue
                others = [one for one in project.tasks if one != task_id]
                mine = self.store.stages([task_id])
                theirs = self.store.stages(others)
                if not mine or not theirs:
                    # Nothing to connect yet: asked again once both have stages.
                    continue
                if not self.going():
                    return done
                made = self._join(project, task_id, mine, theirs, key)
                self.store.set_mark(key, [], [])
                done.append({"joined": task_id, "project": project.id,
                             "edges": made})
        return done

    def _join(self, project: Any, task_id: str, mine: List[Dict[str, Any]],
              theirs: List[Dict[str, Any]], key: str) -> int:
        def listed(stages):
            return "\n".join(
                f"- [{s['id']}] {s['began'][:16]} {s['kind']}: {s['summary'][:220]}"
                for s in stages[-40:])
        asked = (f"THE PROJECT: {project.name}\n{project.why}\n\n"
                 f"ITS STAGES:\n{listed(theirs)}\n\n"
                 f"THE TASK THAT JOINED IT:\n{listed(mine)}")
        answered, response = self._ask(self.in_language(JOIN_PROMPT), asked,
                                       "experience join")
        known = {s["id"]: s for s in mine + theirs}
        made = 0
        for edge in answered.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source, target = str(edge.get("from")), str(edge.get("to"))
            kind = str(edge.get("kind") or "depends_on")
            if source not in known or target not in known:
                continue
            if known[target]["began"] > known[source]["ended"] and \
                    known[source]["ended"]:
                continue
            self.store.add_edge(source, target, kind, reason=key)
            made += 1
        return made

    # -- extending a unit --------------------------------------------------------
    def pending(self, unit: Dict[str, Any]) -> List[str]:
        """The states of a unit not yet seen, plus what was left open - or
        nothing, when nothing is new.

        WHAT WAS LEFT OPEN WAITS FOR SOMETHING NEW. States still in the
        middle of something are shown again with what comes after them;
        shown again on their own, with nothing after them, the answer is
        the same "still open" every time, and a build asked it over and over.
        """
        tail, new = [], []
        for task in unit["tasks"]:
            mark = self.store.mark(task.id)
            seen = set(mark["states_seen"])
            tail += list(mark["open_tail"])
            new += [one for one in task.members if one not in seen]
        new = self._by_the_cutoff(new)
        if not new:
            return []
        return list(dict.fromkeys(tail + new))

    def _by_the_cutoff(self, state_ids: List[str]) -> List[str]:
        """Only the states written by the pass's cutoff, in their order.

        A PASS IS FROZEN WHEN IT OPENS, as a state round is: a state written
        while it runs is the next pass's, so a pass stopped part way carries
        on with exactly the states it began with.
        """
        if not self.until or not state_ids:
            return state_ids
        kept: set = set()
        for start in range(0, len(state_ids), 400):
            chunk = state_ids[start:start + 400]
            holes = ", ".join("?" for _ in chunk)
            for row in self.db.query(
                    f"SELECT id FROM states WHERE id IN ({holes}) AND "
                    f"created_at <= ?", (*chunk, self.until)):
                kept.add(row["id"])
        return [one for one in state_ids if one in kept]

    def extend(self, unit: Dict[str, Any], budget: int = CALLS_PER_ROUND
               ) -> Dict[str, Any]:
        """Put a unit's new states through the stage call, a chunk at a time."""
        report = {"unit": unit["id"], "calls": 0, "stages": 0, "states": 0}
        task_of = {}
        for task in unit["tasks"]:
            for one in task.members:
                task_of[one] = task.id
        pending = self.pending(unit)
        if not pending:
            return report
        rows = {state.id: state for state in self.states.by_ids(pending)}
        ordered = sorted((one for one in pending if one in rows),
                         key=lambda one: _iso(rows[one].starts_at))
        carried = set()
        for task in unit["tasks"]:
            carried.update(self.store.mark(task.id)["open_tail"])
        # As many as last time worked for this piece of work: a batch cut
        # off is not tried at its old size again next round.
        key = (self.scope, unit["id"])
        size = BATCH_SIZE.get(key, STATES_PER_CALL)
        while ordered and report["calls"] < budget and self.going():
            chunk, ordered = ordered[:size], ordered[size:]
            said = self._extend_once(unit, chunk, rows, task_of, carried)
            report["calls"] += 1
            if said.get("error"):
                report["error"] = said["error"]
                break
            if said.get("cut_off"):
                # TOO MUCH FOR ONE ANSWER: the same states again, half as
                # many at a time - and an error, so the round says so, if
                # even the fewest are too many.
                if len(chunk) <= SMALLEST_BATCH:
                    report["error"] = (f"the answer was cut off even for "
                                       f"{len(chunk)} states")
                    log.warning("stages for %s: %s", unit["id"], report["error"])
                    break
                size = max(SMALLEST_BATCH, len(chunk) // 2)
                BATCH_SIZE[key] = size
                ordered = list(chunk) + ordered
                report["cut_off"] = report.get("cut_off", 0) + 1
                continue
            report["stages"] += said.get("stages", 0)
            report["states"] += len(chunk)
            # What was left open is shown again with the next chunk.
            carried = set(said.get("still_open") or [])
            ordered = [one for one in said.get("still_open") or []
                       if one in rows] + ordered
            if len(chunk) <= len(carried):
                # Nothing closed in a whole chunk: wait for more to happen.
                break
        return report

    def in_language(self, prompt: str) -> str:
        """A prompt, told the language its answer is written in."""
        return prompt

    def root_of(self, unit: Dict[str, Any]) -> Dict[str, Any]:
        """The unit's root card - its goal - made the first time it is
        needed, and named after the work as the work is named now."""
        root = self.store.root(unit["id"])
        task_id = unit["id"] if unit["id"] in unit_task_ids(unit) else ""
        if root is None:
            return self.store.add_card({
                "unit_id": unit["id"], "task_id": task_id,
                "title": unit.get("name") or "", "type": "",
                "statement": unit.get("why") or "", "status": "working"})
        fields: Dict[str, Any] = {}
        if unit.get("name") and root["title"] != unit["name"]:
            fields["title"] = unit["name"]
        if not root["statement"] and unit.get("why"):
            fields["statement"] = unit["why"]
        if fields:
            self.store.update_card(root["id"], fields)
            root.update(fields)
        return root

    def tree(self, unit: Dict[str, Any]) -> Tuple[Dict[str, Any],
                                                   Dict[str, Dict[str, Any]]]:
        """The unit's root and every card in its tree, by id.

        ONE ROOT. A card with no parent here - the root of a task that has
        joined this project or been merged into this task, or a card whose
        parent has gone - is put under this root; and this root, if it was
        under a project the task has left, stands on its own again.
        """
        root = self.root_of(unit)
        cards = {card["id"]: card for card in self.store.cards(unit_task_ids(unit))}
        cards[root["id"]] = root
        if root["parent_id"]:
            self.store.update_card(root["id"], {"parent_id": ""})
            root["parent_id"] = ""
        for card in list(cards.values()):
            if card["id"] == root["id"]:
                continue
            seen, here, loops = set(), card, False
            while here.get("parent_id") and here["parent_id"] in cards:
                if here["id"] in seen:
                    loops = True
                    break
                seen.add(here["id"])
                here = cards[here["parent_id"]]
            if loops or here["id"] != root["id"]:
                self.store.update_card(card["id"], {"parent_id": root["id"]})
                card["parent_id"] = root["id"]
        return root, cards

    def _frontier(self, unit: Dict[str, Any], tasks_now: Sequence[str] = ()
                  ) -> Tuple[str, Dict[str, Any], Dict[str, Any],
                             Dict[str, Dict[str, Any]]]:
        """The tree as the writer is shown it, bounded however long the work
        runs: EVERY card, one line each - what stops a second card for the
        same piece; the last attempts only of the cards being worked on now
        (`active_cards`); and a branch that is all done or set aside as one
        line. Anything not shown is an open_card away."""
        root, cards = self.tree(unit)
        stages = self.store.stages(unit_task_ids(unit))
        by_id = {s["id"]: s for s in stages}
        attempts = self._attempts_by_card(stages, root, cards)
        children = self._children(root, cards)
        active = self.active_cards(cards, attempts, tasks_now)
        lines: List[str] = []

        def closed_below(card: Dict[str, Any]) -> int:
            """How many cards are under this one, when every one of them is
            done or set aside - 0 otherwise."""
            below, todo = 0, list(children.get(card["id"], []))
            while todo:
                one = todo.pop()
                if one["status"] not in ("done", "set_aside"):
                    return 0
                below += 1
                todo += children.get(one["id"], [])
            return below

        def show(card: Dict[str, Any], depth: int) -> None:
            pad = "  " * depth
            status = card["status"] + (f" ({card['status_why'][:120]})"
                                       if card["status_why"] else "")
            lines.append(f"{pad}[{card['id']}] {level_of(depth).upper()} · "
                         f"{card['type'] or '-'} · {status}: {card['title']}"
                         + (f" - {card['statement'][:300]}" if card["statement"]
                            else ""))
            mine = attempts.get(card["id"], [])
            if card["id"] in active:
                checked = card.get("checked_by") or {}
                if checked.get("by"):
                    lines.append(f"{pad}    checked by {checked['by']}: "
                                 f"{checked.get('what', '')[:200]}")
                for stage in mine[-3:]:
                    verdict = (stage["evaluation"] or {}).get("verdict", "")
                    lines.append(
                        f"{pad}    [{stage['id']}] {stage['began'][:16]} "
                        f"{stage['kind']}{' (' + verdict + ')' if verdict else ''}"
                        f": {stage['summary'][:300]}")
                if len(mine) > 3:
                    lines.append(f"{pad}    ... {len(mine) - 3} earlier attempts "
                                 f"- open_card to read them")
            elif mine and card["status"] in ("open", "working"):
                lines.append(f"{pad}    ({len(mine)} attempts - open_card to "
                             f"read them)")
            if depth and closed_below(card):
                lines.append(f"{pad}    ({closed_below(card)} cards under it, "
                             f"all done or set aside - open_card to see them)")
                return
            for child in children.get(card["id"], []):
                show(child, depth + 1)

        show(root, 0)
        if not stages and not children:
            lines.append("  (no cards or attempts yet - this is the start of "
                         "the record)")
        return "\n".join(lines), by_id, root, cards

    @staticmethod
    def _attempts_by_card(stages: Sequence[Dict[str, Any]], root: Dict[str, Any],
                          cards: Dict[str, Dict[str, Any]]
                          ) -> Dict[str, List[Dict[str, Any]]]:
        """Each card's stages, in order; one written before there were cards
        is the root's."""
        attempts: Dict[str, List[Dict[str, Any]]] = {}
        for stage in stages:
            card = stage.get("card_id") if stage.get("card_id") in cards \
                else root["id"]
            attempts.setdefault(card, []).append(stage)
        return attempts

    @staticmethod
    def _children(root: Dict[str, Any], cards: Dict[str, Dict[str, Any]]
                  ) -> Dict[str, List[Dict[str, Any]]]:
        children: Dict[str, List[Dict[str, Any]]] = {}
        for card in cards.values():
            if card["id"] != root["id"]:
                children.setdefault(card["parent_id"], []).append(card)
        return children

    @staticmethod
    def active_cards(cards: Dict[str, Dict[str, Any]],
                     attempts: Dict[str, List[Dict[str, Any]]],
                     tasks_now: Sequence[str] = ()) -> set:
        """The cards being worked on now - shown with their attempts: first
        those whose attempts are in the tasks the new states are in, then
        the ones worked on most recently; open or under way, at most
        ACTIVE_CARDS."""
        going = [card for card in cards.values()
                 if card["status"] in ("open", "working")
                 and attempts.get(card["id"])]

        def last(card: Dict[str, Any]) -> str:
            return max(str(s.get("began") or "") for s in attempts[card["id"]])

        wanted = set(tasks_now)
        here = sorted((card for card in going if any(
            s.get("task_id") in wanted for s in attempts[card["id"]])),
            key=last, reverse=True)
        rest = sorted((card for card in going if card not in here),
                      key=last, reverse=True)
        return {card["id"] for card in (here + rest)[:ACTIVE_CARDS]}

    def open_cards(self, unit: Dict[str, Any], ids: Sequence[str]
                   ) -> Dict[str, Any]:
        """The open_card tool: cards of THIS work's tree, whole - what they
        say, the cards under them, and every attempt in order. Nothing
        outside the tree is reachable."""
        root, cards = self.tree(unit)
        stages = self.store.stages(unit_task_ids(unit))
        attempts = self._attempts_by_card(stages, root, cards)
        children = self._children(root, cards)
        out = []
        for card_id in [str(one) for one in ids or []][:6]:
            card = cards.get(card_id)
            if card is None:
                out.append({"id": card_id, "error": "not a card of this work"})
                continue
            out.append({
                "id": card["id"], "level": level_of(depth_of(card, cards)),
                "title": card["title"], "type": card["type"],
                "status": card["status"], "status_why": card["status_why"],
                "replaced_by": card["replaced_by"],
                "statement": card["statement"],
                "specification": card.get("specification") or [],
                "checked_by": card.get("checked_by") or {},
                "why": card.get("why") or {},
                "pieces": [{"id": one["id"], "title": one["title"],
                            "status": one["status"]}
                           for one in children.get(card["id"], [])],
                "attempts": [{
                    "id": stage["id"], "began": str(stage["began"])[:16],
                    "kind": stage["kind"],
                    "verdict": (stage["evaluation"] or {}).get("verdict", ""),
                    "summary": stage["summary"][:600],
                    "decision": {key: ((stage.get("work") or {}).get("decision")
                                       or {}).get(key, "")
                                 for key in ("about", "chose", "by")},
                    "output": ((stage.get("work") or {}).get("output")
                               or {}).get("what", "")}
                    for stage in attempts.get(card["id"], [])]})
        return {"cards": out}

    def _answer(self, unit: Dict[str, Any], system: str,
                messages: List[Dict[str, Any]], record: List[Dict[str, Any]],
                what: str, max_tokens: int = 12000,
                rounds: int = CARD_ROUNDS) -> Tuple[Any, int]:
        """Ask, letting the model open cards of this work for up to `rounds`
        rounds before it answers. Returns the last response and how many
        calls it took; the messages and their record grow as it goes."""
        from ...models.base import ModelRequest
        from .store import count_use
        response, calls = None, 0
        for round_number in range(max(1, rounds)):
            last = round_number == max(1, rounds) - 1
            try:
                response = self.models.invoke(
                    ModelRequest(messages=messages, system=system,
                                 tools=None if last else [OPEN_CARD],
                                 max_tokens=max_tokens, task=model_task(self.scope, "stages"),
                                 language=STAGE_LANGUAGE,
                                 metadata={"kind": what}),
                    role="heartbeat")
            except Exception as exc:     # pragma: no cover - a call
                log.info("could not ask for %s: %s", what, exc)
                return None, calls
            count_use(self.used, response)
            calls += 1
            asked_for = getattr(response, "tool_calls", None) or []
            if not asked_for:
                break
            messages.append({"role": "assistant",
                             "content": getattr(response, "raw_content", "")
                             or response.text})
            record.append({"role": "assistant", "content": response.text or "",
                           "tool_calls": [{"name": call.name,
                                           "arguments": call.arguments}
                                          for call in asked_for]})
            results: List[Dict[str, Any]] = [
                {"type": "tool_result", "tool_use_id": call.id,
                 "content": json.dumps(
                     self.open_cards(unit, (call.arguments or {}).get("ids") or [])
                     if call.name == "open_card" else {"error": "no such tool"},
                     ensure_ascii=False, default=str)[:30000]}
                for call in asked_for]
            if round_number == max(1, rounds) - 2:
                results.append({"type": "text", "text":
                                "That is all the opening there is room for: "
                                "answer now, with the JSON object."})
            messages.append({"role": "user", "content": results})
            record.append({"role": "user", "content": [dict(one) for one in results]})
        return response, calls

    def _keep_references(self, references: List[Dict[str, Any]],
                         members: Sequence[str]) -> List[Dict[str, Any]]:
        """References with their copies: a material's, a commit's, or - for
        a local file no state kept - one made now, as it is now."""
        out = _with_copies(references, self._materials_of(members))
        repos = self._repositories(members)
        out = _split_paths(out, repos)
        for one in out:
            if self.materials is None:
                break
            from .references import access_of
            # A path cut short with "..." - found whole.
            whole = self.materials.whole_path(str(one.get("location") or ""))
            if whole:
                one["location"] = whole
                one["access"] = access_of(one)
            # An agent session - its whole conversation, on this computer.
            transcript = self.materials.transcript(str(one.get("location") or ""))
            if transcript and not one.get("copies"):
                one.update(transcript=transcript, access="local")
            commit = self._commit_in(str(one.get("location") or ""), repos)
            if commit is not None:
                one.update({k: v for k, v in commit.items() if k != "title"},
                           what=one.get("what") or commit.get("title", ""))
            elif not one.get("copies") and \
                    Path(str(one.get("location") or "")).is_file():
                one["copies"] = self.materials.copy_now(Path(one["location"]))
                one["copied"] = "now"
        return out

    def _deliverable(self, said: Dict[str, Any], members: Sequence[str],
                     medium: str, kind: str) -> Dict[str, Any]:
        """What a stage produced: its type, a description, and everything
        that pins it down - its materials with their copies, the locations
        named, and every commit named in it or in the agent's replies it
        cites, looked up in the repositories its states point to."""
        kinds = ("code", "document", "slides", "figure", "email", "message",
                 "data", "form", "other")
        given = str(said.get("type") or "").strip().lower()
        references = [{"material": str(one)[:60], "what": "the deliverable"}
                      for one in (said.get("materials") or said.get("ids") or [])[:12]
                      if str(one).startswith("event_")]
        references += [{"location": str(one.get("location") or "")[:600],
                        "what": str(one.get("what") or "")[:400],
                        "id": str(one.get("id") or "")[:12]}
                       for one in said.get("references") or []
                       if isinstance(one, dict) and str(one.get("location") or "").strip()]
        kept = self._keep_references(references, members)
        # Commits named in its words, or in the replies that are it.
        texts = [str(said.get("excerpt") or "")]
        texts += [_read_copy((one.get("copies") or {}).get("text"), 4000)
                  for one in kept if one.get("kind") == "reply"]
        repos = self._repositories(members)
        have = {one.get("commit") for one in kept if one.get("commit")}
        for text in texts:
            for sha in _HASH.findall(text or ""):
                commit = self._commit_in(sha, repos)
                if commit is not None and commit["commit"] not in have:
                    have.add(commit["commit"])
                    kept.append(dict(commit, what="the code as committed"))
        return {"type": given if given in kinds else (
                    medium if medium in kinds else "other"),
                "excerpt": str(said.get("excerpt") or "")[:1500],
                "references": kept[:16]}

    def _repositories(self, members: Sequence[str]) -> List[Path]:
        """The git repositories the stage's states point to - through their
        sources, and the agent sessions' project folders."""
        found: List[Path] = list(getattr(self, "extra_repos", []) or [])
        places: List[str] = []
        for state_id in members:
            state = self.states.get(state_id) if self.states is not None else None
            meta = getattr(state, "metadata", None) or {}
            places += [str(one) for one in meta.get("sources") or []]
        for one in self._materials_of(members).values():
            location = str(one.get("location") or "")
            places.append(location.split(" in ", 1)[-1] if " in " in location
                          else location)
        for place in places:
            path = Path(place.strip().strip("'\""))
            if not path.is_absolute():
                whole = self.materials.whole_path(str(path)) \
                    if self.materials is not None else None
                if not whole:
                    continue
                path = Path(whole)
            elif "..." in str(path) or "…" in str(path):
                whole = self.materials.whole_path(str(path)) \
                    if self.materials is not None else None
                if not whole:
                    continue
                path = Path(whole)
            for candidate in [path, *path.parents][:8]:
                try:
                    if (candidate / ".git").exists():
                        if candidate not in found:
                            found.append(candidate)
                        break
                except OSError:
                    break
        return found[:6]

    def _commit_in(self, text: str, repos: Sequence[Path]) -> Optional[Dict[str, Any]]:
        if self.materials is None or not repos:
            return None
        for sha in _HASH.findall(text or ""):
            for repo in repos:
                commit = self.materials.commit(repo, sha)
                if commit is not None:
                    return commit
        return None

    # -- from a workspace -------------------------------------------------------
    #: The most model calls one pass over a workspace may make - each call a
    #: round of reading, the last one the answer.
    WORKSPACE_CALLS = 30

    def extend_from_workspace(self, unit: Dict[str, Any], workspace: Any,
                              since: str, until: str,
                              states: Sequence[str] = (),
                              budget: Optional[int] = None) -> Dict[str, Any]:
        """Write a unit's stages from its workspace - and its states, if it
        has any - for what changed after `since` and by `until`: the whole
        history the first time. The model reads the folder with read-only
        tools, in rounds, then answers."""
        from ...models.base import ModelRequest
        from .store import count_use
        from .workspace import WORKSPACE_TOOLS, WorkspaceTools
        budget = max(1, int(budget or self.WORKSPACE_CALLS))
        report: Dict[str, Any] = {"unit": unit["id"], "calls": 0, "stages": 0,
                                  "workspace": str(workspace.root),
                                  "since": since, "until": until}
        if self.models is None:
            report["error"] = "no model"
            return report
        frontier, known, root, cards = self._frontier(unit)
        rows: Dict[str, Any] = {}
        task_of: Dict[str, str] = {}
        for task in unit.get("tasks") or []:
            for one in task.members:
                task_of[one] = task.id
        for one in states:
            state = self.states.get(one) if self.states is not None else None
            if state is not None:
                rows[one] = state
        chunk = [one for one in states if one in rows]
        self.extra_repos = [workspace.root] if workspace.is_repository() else []
        asked = (f"THE WORK: {unit['name']}\n{unit.get('why') or ''}\n"
                 f"{('Status: ' + unit['status']) if unit.get('status') else ''}"
                 f"\n\nTHE CARDS:\n{frontier}\n\n"
                 + (f"THE NEW STATES ({len(chunk)}):\n"
                    + "\n".join(self._state_lines(unit, chunk, rows, task_of))
                    + "\n\n" if chunk else "")
                 + self._workspace_said(workspace, since, until))
        asked += self._known_natures(
            [unit.get("english") or f"{unit['name']} {unit.get('why') or ''}"]
            + [f"{rows[one].title} {str(rows[one].detail or '')[:400]}" for one in chunk])
        tools = WorkspaceTools(workspace, until)
        reading = list(WORKSPACE_TOOLS) + [OPEN_CARD]
        messages: List[Dict[str, Any]] = [{"role": "user", "content": asked}]
        # The record of the pass, whole: what is sent is cut as rounds pass,
        # what is shown to the user is not.
        record: List[Dict[str, Any]] = [{"role": "user", "content": asked}]
        system = self.in_language(STAGE_PROMPT + WORKSPACE_PROMPT)
        response = None
        for round_number in range(budget):
            last = round_number == budget - 1
            try:
                response = self.models.invoke(
                    ModelRequest(messages=messages, system=system,
                                 tools=None if last else reading,
                                 max_tokens=16000, task=model_task(self.scope, "stages"),
                                 language=STAGE_LANGUAGE,
                                 metadata={"kind": "experience workspace"}),
                    role="heartbeat")
            except Exception as exc:     # pragma: no cover - a call
                log.info("could not read the workspace: %s", exc)
                report["error"] = str(exc)[:300]
                return report
            count_use(self.used, response)
            report["calls"] += 1
            calls = getattr(response, "tool_calls", None) or []
            if not calls:
                break
            messages.append({"role": "assistant",
                             "content": getattr(response, "raw_content", "")
                             or response.text})
            record.append({"role": "assistant", "content": response.text or "",
                           "tool_calls": [{"name": call.name,
                                           "arguments": call.arguments}
                                          for call in calls]})
            results: List[Dict[str, Any]] = [
                {"type": "tool_result", "tool_use_id": call.id,
                 "content": json.dumps(
                     self.open_cards(unit, (call.arguments or {}).get("ids") or [])
                     if call.name == "open_card"
                     else tools.call(call.name, call.arguments),
                     ensure_ascii=False, default=str)[:20000]}
                for call in calls]
            if round_number == budget - 2:
                results.append({"type": "text", "text":
                                "That is all the reading there is room for: "
                                "answer now, with the JSON object."})
            messages.append({"role": "user", "content": results})
            record.append({"role": "user", "content": [dict(one) for one in results]})
            _shrink_old_results(messages, keep=4)
        if _cut_off(response):
            # The reading stands; only the answer was too long. Asked once
            # more, for fewer stages - the rest is read next time from where
            # these reach.
            _add_to_last_user(messages, FEWER_STAGES)
            record.append({"role": "user", "content": FEWER_STAGES})
            try:
                response = self.models.invoke(
                    ModelRequest(messages=messages, system=system, tools=None,
                                 max_tokens=16000, task=model_task(self.scope, "stages"),
                                 language=STAGE_LANGUAGE,
                                 metadata={"kind": "experience workspace"}),
                    role="heartbeat")
                count_use(self.used, response)
                report["calls"] += 1
            except Exception as exc:     # pragma: no cover - a call
                report["error"] = str(exc)[:300]
                return report
        if _cut_off(response):
            # Nothing applied, and the folder not marked as read: the round
            # stays open, and says why.
            report["error"] = ("the answer was cut off, even when asked for "
                               "at most six stages")
            log.warning("reading %s: %s", unit["id"], report["error"])
            self.store.save_prompt(unit["id"], "workspace", system, record,
                                   getattr(response, "text", "") or "",
                                   _writer(response), calls=report["calls"])
            return report
        answered = _parsed(getattr(response, "text", "") or "") or {}
        self.store.save_prompt(unit["id"], "workspace", system, record,
                               getattr(response, "text", "") or "",
                               _writer(response) if response is not None else {},
                               calls=report["calls"])
        said = self._apply(unit, answered, chunk, rows, task_of, known,
                           (root, cards), _writer(response), dated=True)
        report.update(said)
        covered = _date_of(answered.get("covered_until"))
        if covered and covered < until:
            report["covered_until"] = covered
        if isinstance(answered.get("project"), dict):
            report["project"] = {key: str(answered["project"].get(key) or "")[:300]
                                 for key in ("title", "goal", "status")}
        return report

    def _workspace_said(self, workspace: Any, since: str, until: str) -> str:
        """What the model is told about the folder before it reads it."""
        commits = workspace.commits(since or None, until)
        turns = workspace.said_in_conversations(since or None, until, most=100000)
        roots = getattr(workspace, "roots", [workspace.root])
        head = ["THE WORKSPACE: " + "; ".join(str(one) for one in roots),
                f"Read from {since or 'the beginning'} to {until}."]
        places = getattr(self, "places_touched", None) or {}
        if places:
            head += ["", "EVERY PLACE THIS WORK TOUCHED, with how much of it did "
                     "(folders above are readable; sites are not):"]
            head += [f"  {share:.2f}  {place}" for place, share in
                     sorted(places.items(), key=lambda kv: -kv[1])[:25]]
        if not since:
            head += ["", "THE FIRST READING OF THIS FOLDER - its whole history.",
                     workspace.overview()]
            if commits:
                oldest, newest = commits[-1], commits[0]
                head += ["", f"COMMITS: {len(commits)}, from {oldest['date'][:10]} "
                         f"({oldest['subject']}) to {newest['date'][:10]}.",
                         "Oldest first:"]
                head += [f"  {c['commit']} {c['date'][:10]} {c['subject']}"
                         for c in reversed(commits[-150:])]
        else:
            changed = workspace.files_changed(since, until)
            head += ["", f"FILES CHANGED SINCE: {len(changed)}"]
            head += [f"  {one['modified'][:16]} {one['path']}"
                     for one in changed[:200]]
            head += ["", f"COMMITS SINCE: {len(commits)}"]
            head += [f"  {c['commit']} {c['date'][:16]} {c['subject']}"
                     for c in reversed(commits[:150])]
        if turns:
            head += ["", f"AGENT CONVERSATIONS IN THIS FOLDER: {len(turns)} turns, "
                     f"{turns[0]['at'][:10]} to {turns[-1]['at'][:10]} - read them "
                     f"with the conversation tool."]
        return "\n".join(head)

    def _nature_writer(self):
        from .stage_natures import NatureWriter
        return NatureWriter(self.library, self.store, self.models,
                            language=self.language, names=self.names,
                            used=self.used, embedder=self.embedder)

    def _known_natures(self, texts: Sequence[str]) -> str:
        """THE USER'S KNOWN NATURES CLOSEST TO THIS WORK, for the answer
        about to be asked for - so a trait the stages show again is said to
        be that trait ("same_as") and becomes one more of its examples, not
        another nature. '' when there are none, or nowhere to write them."""
        if self.library is None:
            self._natures = None
            return ""
        self._natures = self._nature_writer()
        shown = self._natures.show([t for t in texts if str(t).strip()])
        return f"\n\n{shown}" if shown else ""

    def _state_lines(self, unit: Dict[str, Any], chunk: Sequence[str],
                     rows: Dict[str, Any], task_of: Dict[str, str]) -> List[str]:
        by_state = (self.materials.of_states(list(chunk))
                    if self.materials is not None else {})
        lines = []
        for one in chunk:
            state = rows[one]
            meta = getattr(state, "metadata", None) or {}
            extra = ""
            if meta.get("sources"):
                extra += "\n    sources: " + "; ".join(
                    str(s)[:400] for s in meta["sources"][:8])
            lines.append(f"- [{one}] {_when(state)[:16]}\n    {state.title}"
                         f"\n    {str(state.detail or '')[:700]}{extra}"
                         + "".join(f"\n    {said}" for said in
                                   _materials_said(by_state.get(one, []))))
        return lines

    def _materials_of(self, state_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """The materials of these states, by their id."""
        if self.materials is None:
            return {}
        return {one["event_id"]: one
                for rows in self.materials.of_states(list(state_ids)).values()
                for one in rows}

    def _extend_once(self, unit: Dict[str, Any], chunk: Sequence[str],
                     rows: Dict[str, Any], task_of: Dict[str, str],
                     carried: set) -> Dict[str, Any]:
        frontier, known, root, cards = self._frontier(
            unit, [task_of.get(one) for one in chunk if task_of.get(one)])
        titles = {task.id: task_name(task)
                  for task in unit["tasks"]}
        by_state = (self.materials.of_states(list(chunk))
                    if self.materials is not None else {})
        lines = []
        for one in chunk:
            state = rows[one]
            meta = getattr(state, "metadata", None) or {}
            extra = ""
            if meta.get("sources"):
                # In full: the whole path, the whole address - a location
                # taken from here can be opened; one from the prose is cut.
                extra += "\n    sources: " + "; ".join(
                    str(s)[:400] for s in meta["sources"][:8])
            if meta.get("evidence"):
                extra += "\n    evidence: " + "; ".join(
                    str(e)[:200] for e in meta["evidence"][:4])
            in_task = (f" (task: {titles.get(task_of.get(one), '')[:60]})"
                       if unit["kind"] == "project" else "")
            lines.append(f"- [{one}]{' carried' if one in carried else ''} "
                         f"{_when(state)[:16]}{in_task}\n    {state.title}"
                         f"\n    {str(state.detail or '')[:700]}{extra}"
                         + "".join(f"\n    {said}" for said in
                                   _materials_said(by_state.get(one, []))))
        asked = (f"THE WORK: {unit['name']}\n{unit.get('why') or ''}\n"
                 f"{('Status: ' + unit['status']) if unit.get('status') else ''}"
                 f"\n\nTHE CARDS:\n{frontier}\n\n"
                 f"THE NEW STATES ({len(chunk)}):\n" + "\n".join(lines))
        asked += self._known_natures(
            [f"{rows[one].title} {str(rows[one].detail or '')[:400]}" for one in chunk])
        messages: List[Dict[str, Any]] = [{"role": "user", "content": asked}]
        record: List[Dict[str, Any]] = [{"role": "user", "content": asked}]
        response, calls = self._answer(unit, self.in_language(STAGE_PROMPT),
                                       messages, record,
                                       "experience stages")
        self.store.save_prompt(unit["id"], "states", self.in_language(STAGE_PROMPT),
                               record,
                               getattr(response, "text", "") if response else
                               "(the model could not be asked)",
                               _writer(response) if response else {},
                               calls=max(1, calls))
        if response is None:
            return {"error": "the model could not be asked"}
        if _cut_off(response):
            # Nothing is applied, and nothing marked as read: the states are
            # asked about again, fewer at a time.
            return {"cut_off": True}
        said = self._apply(unit, _parsed(response.text) or {}, chunk, rows,
                           task_of, known, (root, cards), _writer(response))
        return said

    def _apply(self, unit: Dict[str, Any], answered: Dict[str, Any],
               chunk: Sequence[str], rows: Dict[str, Any],
               task_of: Dict[str, str], known: Dict[str, Dict[str, Any]],
               tree: Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]],
               writer: Dict[str, str], dated: bool = False) -> Dict[str, Any]:
        """Check the operations, then write them - only adding.

        `dated`: the stages were written from a workspace, and may rest on no
        state - each then carries its own dates, from the commits, files and
        conversation it was read from.

        RESUMABLE. Each stage is written as it is checked, and the marks
        after all of them; a run killed in between leaves stages whose
        states are not yet marked as seen, and the next run reads those
        states again. So a state already in a stage of this work is not
        put in another one - the stage it is in stands, and what was left
        is carried on from.
        """
        chunk_set = set(chunk)
        refs: Dict[str, Dict[str, Any]] = {}
        made = 0
        used: set = {one for stage in self.store.stages(
            unit_task_ids(unit)) for one in stage["states"]}
        root, cards = tree
        natured = 0
        card_refs = self._apply_cards(unit, answered, root, cards, writer)
        made_now_all = {one["id"] for one in card_refs.values()}
        made_now = set(made_now_all)

        def card_named(said: Any) -> Dict[str, Any]:
            said = str(said or "").strip()
            return card_refs.get(said) or cards.get(said) or root
        for entry in answered.get("new_stages") or []:
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("kind") or "").strip().lower()
            members = [one for one in entry.get("states") or []
                       if one in chunk_set and one not in used]
            if kind not in STAGE_KINDS or (not members and not dated):
                continue
            used.update(members)
            owners = [task_of.get(one) for one in members if task_of.get(one)]
            task_id = (max(set(owners), key=owners.count) if owners
                       else unit_task_ids(unit)[0])
            card = card_named(entry.get("card"))
            if card["status"] == "open":
                self.store.update_card(card["id"], {"status": "working"})
                card["status"] = "working"
            # A card made in this answer belongs to the task its attempts
            # are in.
            if card["id"] in made_now and card["task_id"] != task_id and \
                    card["id"] != root["id"]:
                self.store.update_card(card["id"], {"task_id": task_id})
                card["task_id"] = task_id
                made_now.discard(card["id"])
            times = sorted(_iso(rows[one].starts_at) for one in members)
            ends = sorted(_iso(rows[one].ends_at or rows[one].starts_at)
                          for one in members)
            if dated:
                own_began, own_ended = _date_of(entry.get("began")), \
                    _date_of(entry.get("ended"))
                times = sorted(filter(None, [own_began, *times])) or [now()]
                ends = sorted(filter(None, [own_ended or own_began, *ends])) \
                    or [times[-1]]
            medium = str(entry.get("medium") or "").strip().lower()
            evaluation = entry.get("evaluation") if isinstance(
                entry.get("evaluation"), dict) else {}
            # What it produced: the output's example.
            output = entry.get("output") if isinstance(
                entry.get("output"), dict) else {}
            deliverable = output.get("example") if isinstance(
                output.get("example"), dict) else entry.get("deliverable")
            deliverable = deliverable if isinstance(deliverable, dict) else {}
            stage_id = new_id("stage")
            work = _work_of(entry)
            deliverable = _example_of(deliverable, work["references"])
            work["references"] = self._keep_references(
                work["references"], members)
            # An input from a stage named by its ref in this answer is kept
            # by that stage's id; one naming no stage written is just text.
            used_by_input = []
            for one in work["input"]:
                if one["stage"]:
                    target = refs.get(one["stage"]) or known.get(one["stage"])
                    one["stage"] = target["id"] if target else ""
                    if target:
                        used_by_input.append(target["id"])
            # An input read from a material or the folder carries its copies
            # like a reference does.
            for one in work["input"]:
                if one["material"] or one["location"]:
                    kept = self._keep_references(
                        [{"material": one["material"], "location": one["location"],
                          "what": one["what"]}], members)
                    if kept:
                        one.update({k: v for k, v in kept[0].items()
                                    if k not in ("what",)})
            # What it is looked for by, in English (STAGE_PROMPT): kept with
            # its work, and indexed with its own words (index.py).
            work["keywords"] = [str(one).strip()[:60] for one in entry.get("keywords") or []
                                if str(one).strip()][:16]
            stage = self.store.add_stage({
                "task_id": task_id, "card_id": card["id"], "kind": kind,
                "medium": medium if medium in MEDIA else "other",
                "summary": str(entry.get("summary") or "")[:2000],
                "deliverable": self._deliverable(deliverable, members,
                                                 medium, kind),
                "evaluation": {"verdict": str(evaluation.get("verdict") or "")[:40],
                               "criteria": [str(c)[:300] for c in
                                            evaluation.get("criteria") or []][:10],
                               "words": [str(w)[:400] for w in
                                         evaluation.get("words") or []][:10]},
                "states": members, "began": times[0], "ended": ends[-1],
                "id": stage_id,
                "work": work,
                "written_by": writer})
            made += 1
            refs[str(entry.get("ref") or stage["id"])] = stage
            # What it shows about the user, said with it: written, linked to
            # it, and the stage marked as read for it - an empty list is an
            # answer too.
            if self.library is not None:
                natures = self._natures or self._nature_writer()
                for said in entry.get("natures") or []:
                    if natures.write(said, [stage["id"]], writer,
                                     rows={stage["id"]: stage}):
                        natured += 1
                self.store.mark_natured(stage["id"])
            # EVERY INPUT THAT IS AN EARLIER STAGE'S OUTPUT IS AN EDGE - the
            # dependency the graph draws is "this decision used that".
            edges = [one for one in entry.get("edges") or []
                     if isinstance(one, dict)]
            named = set()
            for one in edges:
                to = str(one.get("to") or "")
                target = refs.get(to) or known.get(to)
                named.add(target["id"] if target else to)
            edges += [{"kind": "depends_on", "to": one}
                      for one in dict.fromkeys(used_by_input) if one not in named]
            for edge in edges:
                to = str(edge.get("to") or "")
                target = refs.get(to) or known.get(to)
                edge_kind = str(edge.get("kind") or "continues")
                if target is None or edge_kind not in EDGE_KINDS:
                    continue
                # Only to what came before it.
                if target["began"] and stage["began"] and \
                        target["began"] > stage["began"]:
                    continue
                self.store.add_edge(stage["id"], target["id"], edge_kind)
            known[stage["id"]] = stage
        for changed in answered.get("cards_changed") or []:
            if not isinstance(changed, dict):
                continue
            said = str(changed.get("card") or "").strip()
            card = card_refs.get(said) or cards.get(said)
            status = str(changed.get("status") or "").strip().lower()
            if card is None or status not in CARD_STATUSES:
                continue
            replaced = str(changed.get("replaced_by") or "").strip()
            replacement = card_refs.get(replaced) or cards.get(replaced)
            self.store.update_card(card["id"], {
                "status": status,
                "replaced_by": replacement["id"] if replacement is not None
                and replacement["id"] != card["id"] else "",
                "status_why": str(changed.get("why") or "")[:500]})
        for fix in answered.get("corrections") or []:
            if isinstance(fix, dict) and fix.get("stage") in known and \
                    fix.get("why"):
                fields = {name: fix[name] for name in ("summary", "kind")
                          if fix.get(name)}
                if fields.get("kind") and fields["kind"] not in STAGE_KINDS:
                    fields.pop("kind")
                if fields:
                    self.store.correct_stage(fix["stage"], fields,
                                             str(fix["why"]))
        still_open = [one for one in answered.get("still_open") or []
                      if one in chunk_set and one not in used]
        # The marks: everything in the chunk has been seen; what is still
        # open is shown again next time.
        for task in unit["tasks"]:
            mark = self.store.mark(task.id)
            mine = [one for one in chunk if task_of.get(one) == task.id]
            self.store.set_mark(
                task.id, list(mark["states_seen"]) + mine,
                [one for one in still_open if task_of.get(one) == task.id])
        return {"stages": made, "cards": len(made_now_all),
                "natures": natured, "still_open": still_open}

    def _apply_cards(self, unit: Dict[str, Any], answered: Dict[str, Any],
                     root: Dict[str, Any], cards: Dict[str, Dict[str, Any]],
                     writer: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
        """The answer's new cards, each under the card it names - an id
        shown, a ref made in this answer (in any order), or the root - and
        the root's own words where the answer gave them. Returns the new
        cards by their ref."""
        said_root = answered.get("root") if isinstance(answered.get("root"),
                                                       dict) else {}
        fields: Dict[str, Any] = {}
        if str(said_root.get("statement") or "").strip():
            fields["statement"] = str(said_root["statement"])[:1500]
        wanted = _specification_of(said_root.get("specification"))
        if wanted:
            have = list(root.get("specification") or [])
            known = {one["requirement"] for one in have}
            fields["specification"] = (have + [one for one in wanted if
                                               one["requirement"] not in known])[:20]
        if fields:
            self.store.update_card(root["id"], fields)
            root.update(fields)
        entries = [one for one in answered.get("cards") or []
                   if isinstance(one, dict) and str(one.get("title") or
                                                    one.get("statement") or "").strip()]
        refs: Dict[str, Dict[str, Any]] = {}
        first_task = unit_task_ids(unit)[0]
        # Parents first: a card naming a ref made later in the answer waits
        # for it; one whose parent never comes goes under the root.
        waiting = list(entries)
        while waiting:
            placed_any = False
            for entry in list(waiting):
                parent_said = str(entry.get("parent") or "root").strip()
                if parent_said in ("", "root"):
                    parent = root
                else:
                    parent = refs.get(parent_said) or cards.get(parent_said)
                if parent is None and any(str(other.get("ref") or "") == parent_said
                                          for other in waiting if other is not entry):
                    continue
                parent = parent or root
                card = self.store.add_card(dict(
                    _card_of(entry), parent_id=parent["id"],
                    task_id=parent["task_id"] or first_task,
                    status="open", written_by=writer))
                cards[card["id"]] = card
                refs[str(entry.get("ref") or card["id"])] = card
                waiting.remove(entry)
                placed_any = True
            if not placed_any:
                # Parents that name each other: the first goes under the root.
                waiting[0] = dict(waiting[0], parent="root")
        return refs

    def _ask(self, prompt: str, asked: str, what: str
             ) -> Tuple[Dict[str, Any], Any]:
        if self.models is None:
            return {}, None
        from ...models.base import ModelRequest
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": asked}],
                             system=prompt, max_tokens=12000,
                             task=model_task(self.scope, "stages"), metadata={"kind": what},
                             language=in_english("why each stage leads to another")),
                role="heartbeat")
        except Exception as exc:          # pragma: no cover - a call
            log.info("could not ask for %s: %s", what, exc)
            return {}, None
        from .store import count_use
        count_use(self.used, response)
        return _as_json(getattr(response, "text", "") or ""), response
