"""Working on a piece of the user's work: an agent that works, and an agent
that reviews - only when the user starts it.

Nothing here runs by itself. The user picks a project, or a task in no
project, gives a starting instruction (a new piece of work must have one),
sets the limits, and starts it. Built after Humanize's builder-reviewer
loop (references/humanize_paper_v1.pdf), with the user's own experience as
what the reviewer judges by:

  A CONTRACT FIRST. The work agent drafts the goal, the scope, what is out
  of scope, and the deliverables. The reviewer writes each deliverable's
  ACCEPTANCE CRITERIA from the user's experience - the stages most like the
  work, the natures reached through them, the patterns and principles above
  them - each citing what it rests on, and APPROACH NOTES: how the user
  would go about it, as plain instructions. It approves the contract or
  sends it back, at most three times. The goal is then locked; deliverables
  are added later only by an amendment the reviewer approves.

  THEN ROUNDS. In each the work agent takes one card as far as it can and
  ends with what is now true, each claim with EVIDENCE. Code checks the
  summary before anyone reads it - evidence that exists, the contract
  untouched - and sends it straight back if not. The reviewer, in a fresh
  context with the experience retrieved for that card, opens the evidence
  and returns findings (a gap in the card, a blocking problem, or one that
  can wait - which becomes a card), whether the work advanced, stalled or
  regressed, and whether the goal is met. Every fifth round is a FULL review
  of every criterion. A second stall in a row brings a prompt to step back;
  a third ends the run. A card that cannot move without something missing
  is set aside with what it needs.

  THEN A FINAL REVIEW of everything the work changed, with at most three
  rounds of fixes, and A DECK of the results - what was produced, where it
  is, and its key variables explained.

THE WORK AGENT never sees the user's experience: only the criteria and
approach notes, as plain requirements. THE REVIEWER decides; the user is
not asked. Choices that were really the user's are logged, with how far
their past work supported each, for them to check afterwards.

A LIMIT PAUSES THE RUN, it does not rush it: new work stops while some of
each limit is left, and that remainder goes to the deck of what exists so
far and a to-do list. The user can CONTINUE it later with new limits, from
exactly where it stopped. A run going when the application closed is
marked interrupted, and continued the same way - never by itself.

It writes ONLY in the work's own folder under the workspace's `agent_work`,
and in the folders the user granted - enforced by the runtime, not asked of
the model (see `tools.py`). Code it runs opens no network connection. What
it does is recorded as the AGENT'S activity, never the user's.

PACKAGES it needs are installed into the run's own folder, by pip running
inside the same guard (see `packages.py`). The user chooses, per run, to be
ASKED before each install - the run waits for their answer, and the wait
does not count against its time - or to let them go in AUTOMATICALLY; either
way every install is in the log and the report.

A VERSION is saved at each key point - the contract approved, a round that
advanced, a full review, the final review, the deck, the end of a session -
holding the work's files and where the run stood (see `versions.py`). The
run can be continued from ANY of them: an older one rolls the files and the
state back to it and goes on as a new branch, the later line kept.

CONTINUED WITH NEW WORDS - "what to change from here" - the run first
AMENDS ITS CONTRACT for that line, before any more work: the work agent
drafts the amended contract from the one it had, the user's words VERBATIM
and where the work stands - every clause the words do not touch copied word
for word - and the reviewer checks it against the words, as it checked the
contract, approving it or sending it back; the user is not asked. The words
are kept inside the contract, as they were said, so both agents read them
for the rest of that line; what changed is shown clause by clause; work
already done that no longer holds under it is opened again. The amended
contract lives in the plan: the versions after it hold it, and the other
line keeps its own.

THE LIVE SESSION is what the page shows while it happens, like a terminal:
each model turn of the work agent and the reviewer, the tools they call,
the checks, the verdicts, the versions saved, the installs - drawn from the
same hooks that keep the log, held in memory (the last few thousand) and
kept in the run's folder, so a page opened later shows what went before.
The user can PAUSE the run at any time: it stops at its next safe point -
before a model call, before a tool call, between rounds - and does nothing
until resumed, then goes on exactly there, its conversation still in
memory; paused time is not counted against its minutes. That memory lives
in the application: closing the page changes nothing, but a run going or
paused when the APPLICATION closes is interrupted, and continues from its
last milestone - the latest version.
"""

from __future__ import annotations

import difflib
import glob
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from ...common.sound import DONE as SOUND_DONE, FAILED as SOUND_FAILED
from ...common.sound import STOPPED as SOUND_STOPPED, play_end_sound
from ...heartbeat.experience.store import description_of, input_of
from ...models.base import ModelRequest
from ...common.languages import in_display, in_english

#: WHICH LANGUAGE A WORK RUN WRITES IN, unless a call says otherwise (the
#: search words do): what the user reads of it in their display language;
#: what it makes for the work itself in whatever language that work needs.
#: THE README THAT GOES WITH WHAT WAS DELIVERED: every run that made
#: something ends with one, in the work's folder - how to check it and use it.
README_PROMPT = """WRITE THE README THAT GOES WITH WHAT WAS DELIVERED. The user reads it
first, and did not watch the work. In short headed sections of plain Markdown:

- WHAT WAS MADE, in a sentence or two;
- WHERE IT IS: each file or change, by its path;
- HOW TO CHECK IT: the steps that show it does what was asked - what to open
  or run, and what they should see. This is how they accept the work;
- HOW TO USE IT: how to open, run or apply it, with the exact commands where
  there are any;
- WHAT IS NOT DONE, if anything, and what would come next.

Write only what the files show: open them with your tools before you write,
and do not give a step you have not checked.{unfinished}

Return ONLY this JSON object: {{"readme": "the whole README, as Markdown"}}"""
#: The first line of a README a run wrote - one without it is the user's,
#: never written over.
README_MARK = "<!-- written by the agent's run: how to check and use what it made -->"

WORK_LANGUAGE = in_display(
    "every card, summary, slide, note and report the user reads - what you MAKE for "
    "the work itself (a document, a reply, code) is in whatever language that work needs")
from ...runtime.permissions import SecurityViolation, WorkspacePolicy
from ...runtime.sandbox import SandboxRuntime
from .packages import ASK, AUTO, Installer, PACKAGES_DIR, mode_of, validate
from .tools import WorkTools, compact_experience, experience_text, stage_key_info
from .transcript import Transcript, answer_text, ensure_readme, transcript_files
from ...version import APP_NAME
from .versions import (LOGS_DIR, SLIDES_DIR, VERSIONS_DIR, VersionError, VersionStore,
                       file_changes, keep_files, keep_granted, keep_tree, restore_files,
                       restore_granted, restore_tree, sha_of_file, with_message)

log = logging.getLogger(__name__)

#: The directory under the workspace that holds every piece of work's folder.
WORK_DIR = "agent_work"

#: What a run may spend unless the user says otherwise. Zero - or less -
#: is no limit on that one.
#:
#: TOKENS IN AND TOKENS OUT ARE LIMITED APART. What goes in is counted
#: whole - the input read from the provider's prompt cache too (see
#: `input_of` in the experience store) - and is by far the larger: the
#: work runs this application had made read about 42,000 tokens a call
#: (nearly all of it cached) and wrote about 1,800. One limit on the two
#: together said nothing of either. So: eight million in - some two hundred
#: calls of that size, what the limit on calls allows - and four hundred
#: thousand out, room for two hundred answers a good deal longer than most.
#: BY DEFAULT, NO LIMIT (the user's decision, 2026-10-12): a run goes until
#: its work is done, or it is stopped - 0 is no limit. A limit the user
#: sets is kept as set.
DEFAULT_LIMITS = {"minutes": 0, "tokens_in": 0, "tokens_out": 0, "calls": 0}

#: A run started before the two were limited apart, or by a caller that
#: still sends one limit: "tokens", on what went in and came out together.
#: Honoured as given; a run started with it has no limit on either alone
#: unless one is given too.
TOTAL_TOKENS = "tokens"

#: How each limit is named when a run stops at it.
LIMIT_NAMES = {"minutes": "time", "tokens_in": "input token",
               "tokens_out": "output token", TOTAL_TOKENS: "token", "calls": "call"}

#: Kept back from each limit for the wrap-up - the deck and the to-do list:
#: this share of it, at least the floor, and never more than half.
RESERVE_SHARE = 0.10
RESERVE_FLOOR = {"minutes": 5, "tokens_in": 400_000, "tokens_out": 20_000,
                 TOTAL_TOKENS: 80_000, "calls": 8}

#: Rounds of tools one answer may use before it must answer: a review or a
#: draft, and a round of work.
TOOL_ROUNDS = 14
WORK_TOOL_ROUNDS = 40

#: A VERSION'S SLIDES are drawn in a few rounds of tools - a handful of
#: figures, not a second round of work - and with these tools alone: the
#: reading ones, run_python and write_file to draw (into the version's own
#: slides folder, the only place they write), and install_packages for what
#: drawing needs (as the run's packages mode says). No web, no cards.
SLIDES_TOOL_ROUNDS = 8
SLIDES_TOOLS = ("list_dir", "read_file", "find_files", "write_file", "run_python",
                "install_packages")

#: What one deck of slides may show: so many figures, each so large at
#: most - each is kept in the version, and carried in the page that shows it.
MOST_FIGURES = 16
MOST_FIGURE_BYTES = 6 * 1024 * 1024

#: Times the reviewer may send the contract back before it is approved -
#: an amendment of it too.
CONTRACT_REVIEWS = 3

#: The most of the user's words one continue carries.
INSTRUCTION_CHARS = 4000

#: Times a summary failing the checks goes back within one round.
GATE_RETRIES = 2

#: Every so many rounds the review is of every criterion, not one card.
FULL_REVIEW_EVERY = 5

#: Stalled or regressed rounds in a row: at the first number the agent is
#: told to step back; at the second the run ends.
STEP_BACK_AT = 2
STALLS_TO_STOP = 3

#: Rounds of fixes after the final review.
FIX_ROUNDS = 3

#: Cards given their criteria at the start of one round.
CRITERIA_PER_ROUND = 3

#: Times the deck may go back before it is kept as it is.
DECK_REVIEWS = 2

#: How much of the retrieved experience the reviewer is shown: the rules
#: whole, each with up to five cases, and the stages as a line each - what
#: does not fit is left out whole, never cut (tools.experience_text). Cut at
#: 40,000 characters of whole stages, the rules after them were never seen.
EXPERIENCE_CHARS = 90000

#: THE USER'S RECORD IS SEARCHED IN ENGLISH, by what matters: what is asked
#: about - an instruction in the user's words, a card - said first as the
#: English words the work would be recorded under.
SEARCH_WORDS = """You turn a piece of work into the words to search a person's record of
their past work for: what it is about and what could go wrong with it.

Answer with JSON only: {"keywords": ["8 to 15 words or short phrases"]}

Concrete: the kind of
thing being built or done, its domain, the parts it needs, the risks and
failure cases that matter for it (money or cost, limits, errors from other
services, many users at once, data loss, security), how it is checked.
Not filler, not the person's name, not words any work would have."""

#: THE CORE PROMISES: what the user must get, said as behaviour tried for
#: real - drafted with the contract, checked first by every review, and
#: the work not complete until each holds. A run built a polished platform
#: whose pledge was never a real cap on spending: what the user asked for
#: had never become something anyone tried.
CORE_PROMISES_RULES = """THE CORE PROMISES are what the user must get - every requirement in their
own words, each said as a behaviour they would try themselves ("a donor
cannot be charged more than they pledged", not "a pledge model exists").
Each says how it is tried for real and the failures it must survive - those
that matter to the user: money, limits, errors from the services it uses,
many users at once, data lost. A requirement the user did not state is not
a promise.

TIERS: a "core" deliverable is one without which a promise cannot hold;
"supporting" is what core ones need (setup, data, tests); "polish" is the
rest - looks, extras, convenience. No polish is worked on until every core
promise holds. Each deliverable names the promises it serves."""

TIERS = ("core", "supporting", "polish")

#: WHAT ONLY THE USER CAN CHECK OR DO - kept as a list, every milestone
#: adding what it found (the contract, each reviewed round, the tester, the
#: final review), and kept as short as the work can make it. A promise
#: that cannot be tried here at all is one of them: it does not hold the
#: run open - a run that could never try it would go on round after round.
FOR_USER_NOTE = """FOR THE USER - AS LITTLE AS YOU CAN: "for_user" lists only what the work
truly cannot do or check itself - a real payment, the user's own account or
device, a key or access they have not given, a judgement only they can
make. Before you list one, try what can stand in for it - a test mode, a
sandbox account, a mock, a local copy - and say in "why" what you tried.
Everything else the work does itself. Most of the time the list is empty."""

NEEDS_USER = """"needs_user" only for a promise that cannot be tried here at all, even
with a stand-in: then "user_does" says what the user must do to check it."""

#: What a run ends as, and which it can be continued from.
DONE = "done"
NOT_FINISHED = "not finished"
STALLED = "stopped: stalled"
STOPPED_BY_REVIEW = "stopped by the reviewer"
BLOCKED = "blocked"
NO_CONTRACT = "no contract"
INTERRUPTED = "interrupted"
CONTINUABLE = (NOT_FINISHED, STALLED, STOPPED_BY_REVIEW, BLOCKED, INTERRUPTED)

#: A run waiting for the user to answer a request to install packages.
WAITING = "waiting for you"

#: A run the user paused: held at its next safe point until they resume it.
#: Only ever the status of a run that is going - one the application closed
#: on is settled as interrupted.
PAUSED = "paused"

#: The live session: the most events held in memory per run, how many one
#: answer carries, and where they are kept - in THE PROJECT'S RUN LOGS, the
#: run's own folder in `run_logs/` (see `log_dir_for`), beside its full
#: transcript, and left out of every version (see `versions.LOGS_DIR`),
#: because what happened stays what happened whichever version the work
#: goes on from. SESSION_DIR is where a run kept them before the logs had a
#: folder of their own - inside its run folder; such a run goes on reading
#: and writing them there.
SESSION_EVENTS = 2000
SESSION_BATCH = 500
SESSION_DIR = ".session"
SESSION_FILE = "events.jsonl"
#: How much of the end of the kept file is read back - a run continued many
#: times has a long one, and only its last events are shown.
SESSION_TAIL_BYTES = 2 * 1024 * 1024

#: The whole of a live session's line, kept with its short preview - a long
#: argument, code, an answer. Past this the rest is in the run's transcript.
EVENT_FULL_CHARS = 100_000

#: A RUN'S MODEL CALLS, one by one, for the chart of them over time: the
#: most kept in its plan - the latest, across its sessions - and the most
#: one look at the live session carries. A call is a line of a few numbers:
#: six hundred of them are a few tens of kilobytes.
CALLS_KEPT = 600
CALLS_BATCH = 600

#: Who made a call, as the chart names them: the work agent is the builder;
#: the Experience debugger's first way, the single model.
CALL_WHO = {"worker": "builder", "reviewer": "reviewer"}

#: What the page shows a run's state as, from what it is doing.
LIVE_GOING = "going"
LIVE_PAUSED = "paused"
LIVE_WAITING = "waiting_for_you"
LIVE_ENDED = "ended"
LIVE_INTERRUPTED = "interrupted"

#: The kinds of version a session ends with, unfinished: each has the to-do
#: list it ended with.
UNFINISHED_ENDS = ("paused", "stopped", "limit reached", "ended")

#: What a request to install packages can be: waiting, answered either way,
#: never answered (the run stopped, or the application closed), and how the
#: install went.
PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"
UNANSWERED = "not approved"
INSTALLED = "installed"
FAILED = "failed"

#: How often a run waiting for an answer looks to see if it was stopped.
WAIT_STEP = 0.5

#: A MODEL CALL THAT TIMED OUT, OR FOUND ITS PROVIDER UNAVAILABLE FOR THE
#: MOMENT, IS MADE AGAIN - after each of these waits, in seconds, and then
#: after each of CALL_WAIT_LONGER. A provider that did not answer in five
#: minutes once is not a provider that never will, and a run of an hour
#: lost to one slow answer is a run the user has to start again. What the
#: user must act on is waited on instead (see `model_trouble`).
CALL_RETRY_WAITS: Tuple[float, ...] = (5.0, 20.0)


def retryable_failure(exc: BaseException) -> bool:
    """Whether a failed model call is worth making again: the provider
    timed out, or was unavailable, overloaded or out of reach for now -
    never a sign-in, a credential, a quota, a permission or a request the
    provider refused, which another attempt would only fail the same way."""
    from ...models.base import (AvailabilityState, FailureClass,
                                ModelInvocationError)
    from ...models.manager import ModelUnavailable
    if not isinstance(exc, (ModelInvocationError, ModelUnavailable)):
        return False
    availability = getattr(exc, "availability", None)
    if availability is not None:
        if availability.requires_user_action():
            return False
        if availability.failure_class is FailureClass.TEMPORARY:
            return True
        if availability.state in (AvailabilityState.PROVIDER_UNAVAILABLE,
                                  AvailabilityState.NETWORK_UNAVAILABLE,
                                  AvailabilityState.RATE_LIMITED):
            return True
    text = str(exc).lower()
    # And a client that started without its input: Claude Code waits only a
    # few seconds for it (common/children._input_file) - the next try has it.
    return any(one in text for one in ("did not answer within", "timed out", "timeout",
                                       "no stdin data received"))


#: THEN, FOR AS LONG AS IT LASTS: a call that failed for the moment is made
#: again after each of these waits - the last one again and again - until
#: the model answers or the run is stopped. A run is not lost to a failure
#: that passes.
CALL_WAIT_LONGER: Tuple[float, ...] = (60.0, 120.0, 300.0, 600.0)
#: BUT NOT FOREVER: a call that has failed for the moment for this long -
#: each attempt perhaps spending usage before it was cut off - is not made
#: again on its own. The run waits for you instead (MODEL_STUCK), and makes
#: it again only when you say so.
TEMPORARY_CAP_SECONDS = 3600.0

#: A USAGE LIMIT is looked at again a minute after the time it said it
#: resets - or, when it did not say, this often.
LIMIT_LOOK_SECONDS = 600.0
LIMIT_RESET_GRACE = 60.0
#: A MODEL ONLY THE USER CAN MEND is looked at again after the first of
#: these, then the second, again and again: mended in Settings, or outside
#: this application, the run goes on without being told.
YOURS_LOOK_SECONDS: Tuple[float, ...] = (60.0, 600.0)
#: Never looked at again sooner than this.
LOOK_AT_LEAST = 60.0

#: WHAT A FAILED MODEL CALL ASKS OF THE RUN (`model_trouble`):
#:   TEMPORARY - timed out, or its provider unavailable for the moment: made
#:     again on its own, and the run goes on.
#:   LIMIT - a usage limit, which resets by itself: the run waits - for you
#:     to see, and to end it or switch the model if you will - and looks
#:     again when it resets.
#:   YOURS - only you can mend it: a sign-in, a key, a model not offered,
#:     credit. The run waits for you, and says where to mend it.
MODEL_TEMPORARY = "temporary"
MODEL_LIMIT = "limit"
MODEL_YOURS = "yours"
#: Failed for the moment for TEMPORARY_CAP_SECONDS: waits for you, and is
#: not looked at again by itself.
MODEL_STUCK = "stuck"

#: How a wait on the model ended: the model answered again, you ended the
#: run, or the model failed another way (and is waited on for that).
CONTINUED = "continued"
ENDED = "ended"
REPLACED = "replaced"

_USAGE_LIMIT_WORDS = ("usage limit", "session limit", "weekly limit", "limit reached",
                      "hit your limit", "resets", "try again in", "try again at")
#: A quota that only paying mends - no reset will.
_PAID_WORDS = ("credit", "billing", "balance", "payment")


def model_trouble(exc: BaseException) -> Optional[str]:
    """What a failed model call asks of the run - MODEL_TEMPORARY,
    MODEL_LIMIT or MODEL_YOURS - or None for a failure that ends it."""
    from ...models.base import AvailabilityState, ModelInvocationError
    from ...models.manager import ModelUnavailable
    if not isinstance(exc, (ModelInvocationError, ModelUnavailable)):
        return None
    availability = getattr(exc, "availability", None)
    text = f"{exc} {getattr(availability, 'reason', '') or ''}".lower()
    if availability is not None:
        state = availability.state
        if state is AvailabilityState.QUOTA_EXHAUSTED:
            return MODEL_YOURS if any(one in text for one in _PAID_WORDS) else MODEL_LIMIT
        if state is AvailabilityState.RATE_LIMITED and any(
                one in text for one in _USAGE_LIMIT_WORDS):
            return MODEL_LIMIT
        if availability.requires_user_action():
            return MODEL_YOURS
    if retryable_failure(exc):
        return MODEL_TEMPORARY
    return None


_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_RELATIVE_RESET = re.compile(
    r"(?:resets?|try again|retry|available again)\s+in\s+"
    r"((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b[\s,]*(?:and\s+)?)+)",
    re.IGNORECASE)
_CLOCK_RESET = re.compile(
    r"(?:resets?|try again|available again)\s+(?:at\s+|on\s+)?"
    r"(?:([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(?:at\s+)?)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s?m\b\.?)?"
    r"(?:\s*\(([^)]+)\))?", re.IGNORECASE)


def limit_resets_at(text: str, now: Optional[float] = None) -> Optional[float]:
    """WHEN A USAGE LIMIT RESETS, as the provider said it - "resets 11:50pm
    (Europe/Lisbon)", "resets Oct 12, 3pm", "try again in 2 hours 5
    minutes", "try again at 6:01 PM", or the moment itself after a "|" - in
    seconds since the epoch. None when it did not say, or said what cannot
    be read: then the run looks again every LIMIT_LOOK_SECONDS."""
    now = time.time() if now is None else float(now)
    text = str(text or "")
    found = re.search(r"\|\s*(\d{10})\b", text)
    if found:
        return _sane_reset(float(found.group(1)), now)
    found = _RELATIVE_RESET.search(text)
    if found:
        seconds = {"d": 86400, "h": 3600, "m": 60, "s": 1}
        total = sum(int(amount) * seconds[unit[0].lower()] for amount, unit in
                    re.findall(r"(\d+)\s*([a-z]+)", found.group(1), re.IGNORECASE))
        if total:
            return _sane_reset(now + total, now)
    for found in _CLOCK_RESET.finditer(text):
        month_word, day, hour, minute, half, zone = found.groups()
        if minute is None and half is None:
            continue
        hour = int(hour)
        if half:
            if hour > 12:
                continue
            hour = (hour % 12) + (12 if half.lower() == "p" else 0)
        if hour > 23 or int(minute or 0) > 59:
            continue
        tz = None
        if zone:
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(zone.strip())
            except Exception:
                tz = None
        base = (datetime.fromtimestamp(now, tz) if tz is not None
                else datetime.fromtimestamp(now).astimezone())
        month = _MONTHS.get((month_word or "")[:3].lower()) if month_word else None
        try:
            when = base.replace(hour=hour, minute=int(minute or 0), second=0, microsecond=0)
            if month and day:
                when = when.replace(month=month, day=int(day))
                if when.timestamp() < now - 60:
                    when = when.replace(year=when.year + 1)
            elif when.timestamp() < now - 60:
                when = when + timedelta(days=1)
        except ValueError:
            continue
        return _sane_reset(when.timestamp(), now)
    return None


def _sane_reset(at: float, now: float) -> Optional[float]:
    """A reset time to wait for: not long past, and within eight days."""
    return at if now - 60 <= at <= now + 8 * 86400 else None


def _clock(at: float) -> str:
    """A moment as the user reads it here: "23:50", or "Oct 12 23:50" when
    it is not today."""
    when = datetime.fromtimestamp(at).astimezone()
    today = datetime.now().astimezone().date()
    return when.strftime("%H:%M") if when.date() == today else when.strftime("%b %d %H:%M")


def _iso_at(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def _seconds_said(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{round(seconds / 3600, 1)} h"


class ModelShut(Exception):
    """A wait on the model ended the run - you ended it, or stopped it while
    it waited: no other model call is made in this session."""

#: How long a run takes its project, as the record has it, as it was read:
#: learning going on beside it adds to it (`Run.live`).
LIVE_FRESH_SECONDS = 30.0

#: Ids of the user's experience - never passed to the work agent.
EXPERIENCE_ID = re.compile(r"\b(?:stage|rec|pattern|principle|nature)_[0-9a-zA-Z]{6,}\b")

#: THE WAYS A RUN CAN BE MADE - for the Experience debugger, which does the
#: same work three ways side by side (see `debug_projects.py`):
#:   FULL - the whole system: the contract, the rounds, a reviewer who
#:     judges by the user's experience. Every run of the user's own work is
#:     this, and nothing below changes it.
#:   NO_CONTEXT - the same builder and reviewer, the contract and the rounds,
#:     but the reviewer is given NOTHING of the user's experience: no past
#:     stages, no natures, no patterns or principles, no search of them.
#:   SINGLE - one model on its own: the task as the user gave it, the same
#:     tools to read, run, search and write, and no contract, no cards, no
#:     reviewer. It works until it says it is done.
FULL = "full"
NO_CONTEXT = "noctx"
SINGLE = "single"

#: Where a single model's conversation is started again - from the task and
#: its own reports so far - rather than grow past what a model can read.
ALONE_CONTEXT_CHARS = 400_000


def work_scope(unit_id: str) -> str:
    """Where a piece of work's own tree is kept, apart from the user's."""
    return f"work:{unit_id}"


#: What code a run runs may reach, as the prompts say it (`network_rules`).
NETWORK_OWN_LOOPBACK = """Code you run opens no network connection but to
servers its own run starts on this computer: a web app or service you build
can be started and tested for real. Take a free port by binding a socket to
("127.0.0.1", 0), reading its port and closing it - never a fixed port:
other runs may be going at the same time - and start the server on
127.0.0.1 with it. A server lives only as long as the run_python call that
started it: start it with subprocess.Popen([sys.executable, ...]) in the
same script, wait until it answers, test it with a client (urllib,
requests, httpx), then stop it. Nothing else on this computer is reachable -
no other run's servers, nothing of the user's; read pages with fetch_url.
The framework's test client (Flask's app.test_client(), FastAPI's
TestClient, Django's Client) remains the quickest check."""

NETWORK_NONE_AT_ALL = """Code you run opens no network connection -
not even to a server it starts itself on localhost; read pages with
fetch_url. To test a web app you build, call it in the same process: the
framework's test client (Flask's app.test_client(), FastAPI's TestClient,
Django's Client) or the WSGI/ASGI app directly."""


#: Added where a run has the testing browser (runtime/browser.py).
NETWORK_BROWSER = """A BROWSER is there for testing what you build, as a
person would see it: a headless Chrome of this run's own, with an empty
profile, which opens only the servers this run starts. In the same
run_python script as the server:
  from agent_browser import Browser, free_port, wait_for_server
  port = free_port()  # start the server on it, then wait_for_server(url)
  with Browser() as page:          # Browser(width=390, height=844) for a phone
      page.goto(f"http://127.0.0.1:{port}/")
      page.screenshot("screenshots/home.png")   # full_page=True, selector="#x"
      page.type("#email", "a@b.c").click("button[type=submit]")
      page.wait_for(".done"); print(page.text(".done"))
      page.press("Enter"); page.select("#size", "l"); page.evaluate("js")
      print(page.errors)           # the page's script errors and failed loads
Check what the page shows with page.text(...), page.exists(...) and
page.errors - you cannot see the pictures yourself. Keep the screenshots in
your folder: evidence the user and the reviewer can open, and figures for the
slides."""


def zone_rule() -> str:
    """THE USER'S TIMEZONE, for every system prompt of a run: a time the
    model writes - on a slide, in a summary, in a file - in it, with its
    offset; not UTC, which it took to be the time otherwise. Only the zone:
    it changes with daylight saving and nothing else, so the prompt the
    provider caches stays the same call after call."""
    here = datetime.now().astimezone()
    shift = here.utcoffset() or timedelta(0)
    total = int(shift.total_seconds())
    sign = "-" if total < 0 else "+"
    hours, rest = divmod(abs(total), 3600)
    zone = f"{here.tzname() or 'local time'}, UTC{sign}{hours:02d}:{rest // 60:02d}"
    return (f"THE USER'S TIMEZONE IS {zone}. Every time you write - on a slide, in a "
            f"summary, in a file, in a chart's labels - is in it, with its offset "
            f"(e.g. {here.replace(hour=9, minute=30, second=0, microsecond=0).isoformat()}), "
            f"never UTC unless it says "
            f"so. A time you read that is in UTC, convert. For the time now, read the "
            f"clock (run_python: datetime.now().astimezone()).")


def today_line() -> str:
    """Today, in the user's timezone - changing once a day."""
    return f"TODAY: {datetime.now().astimezone().strftime('%A %d %B %Y')} (the user's timezone)"


NATIVE_SHUT = """NATIVE LIBRARIES CANNOT LOAD HERE: the code you run has no operating-system
sandbox around it on this computer, so loading a native library (ctypes) is
refused - and with it every package that loads one as it is imported:
uvicorn, click, colorama, and what is built on them. Do not try them. Serve
a web app with the standard library (wsgiref, http.server) and test it in
process: Flask's app.test_client(), FastAPI's TestClient over the app, or
calling the WSGI app directly."""


def native_code_loads() -> bool:
    """Whether the code a run starts may load native libraries: only where
    the operating system confines it on its own (runtime/guard.py,
    `_native_allowed` - the same backends)."""
    import sys
    from ...runtime.isolation import isolation_status
    try:
        status = isolation_status()
    except Exception:
        return False
    return (bool(status.get("isolated"))
            and status.get("backend") in ("appcontainer", "seatbelt", "bubblewrap")
            and not getattr(sys, "frozen", False))


def native_rule() -> str:
    """What the agents are told when native libraries cannot load: which
    packages will not import, and what to use instead - before they find
    it out by trying. Nothing where they load."""
    return "" if native_code_loads() else "\n\n" + NATIVE_SHUT


ENVIRONMENT_RULES = """THE ENVIRONMENT - EXACTLY WHAT YOU CAN AND CANNOT DO. Your tools and all
the code you run are held to it. A refusal says what was refused and why: do
it the allowed way - there is no other route around it, every route is
refused the same way.
  READ: any file or folder under the user's home, with your tools or code -
    EXCEPT these, refused however it is tried (a listing leaves them out):
    this project's run logs (its run_logs folder: the record of what
    happened); the application's own settings and state; where sign-ins and
    keys are kept (AppData, .ssh and the like){more_closed}.
  WRITE: ONLY in {folders} - with write_file, delete_file or code you run.
    Everywhere else is read-only, to everything: a SQLite database there in
    WAL mode cannot be opened even read-only - copy it into your folder
    first. The contract's own files are not written by any means: what code
    changes in them is put back.
  RUN CODE: each run_python call is a NEW Python process - only files carry
    over to the next call: no variables, no imports, no running programs. It
    starts in the work's folder. The call waits for the code to end -
    timeout seconds, {run_seconds} unless you ask, at most {run_most} - and
    returns what it printed and the files it changed. WHATEVER THE CODE
    STARTS ENDS WHEN IT ENDS: a program started and not waited for is
    stopped with it, never left running in the background. For anything
    longer, or to go on working while it runs: run_python with
    background=true - a job of up to {job_most} seconds - then job_status
    with wait (up to {job_wait} seconds at a time) until it ends. Do not
    sleep in code to wait for what another call started: nothing another
    call started is still running, but a job.
  CHILD PROCESSES: Python only - subprocess with the command as a list
    ([sys.executable, '-m', 'pytest', ...], a script, -c code); each runs
    under these same rules. No other program starts: no shell
    (shell=True), no git, node, npm, cmd or PowerShell; no new process group
    or session.
  NETWORK: {network}
  {native}
  PYTHON PACKAGES: with install_packages only. pip run from code cannot
    reach PyPI.
  THE WEB: web_search for words; fetch_url only for an address that already
    exists - a search result, a page's link, the user's files and
    instruction, their recorded work; one you put together is refused. A
    query or an address that carries anything like a password, a key, a
    token or encoded data is refused."""

NATIVE_LOADS = """NATIVE LIBRARIES: ctypes, and the packages that load a native
library as they are imported, work - under these same rules."""


def environment_rules(folders: str, network: str, debug: bool = False) -> str:
    """THE ENVIRONMENT, EXACTLY: what the work agents may read, write, run
    and reach, and what is refused - every limit and every prohibition in
    one place, as plainly as they are enforced, so that a habit from another
    environment (a job left running in the background, a database opened in
    a read-only folder) is not found out by failing."""
    from .tools import (JOB_MOST_SECONDS, JOB_MOST_WAIT, RUN_MOST_SECONDS,
                        RUN_SECONDS)
    native = NATIVE_SHUT if not native_code_loads() else NATIVE_LOADS
    return ENVIRONMENT_RULES.format(
        folders=folders, network=network, native=native,
        # NOT SAID WHY: a way that is told it is one of three in a debugger
        # knows it is being compared - the thing its letter-named folder is
        # there to keep from it.
        more_closed=("; other folders closed to this work, and the application's "
                     "own code" if debug else ""),
        run_seconds=RUN_SECONDS, run_most=RUN_MOST_SECONDS, job_most=JOB_MOST_SECONDS,
        job_wait=JOB_MOST_WAIT)


def network_rules(browser: bool = False) -> str:
    """Where the operating system gives each run a loopback of its own
    (runtime/isolation.own_loopback), its code may test the servers it
    starts - and with the testing browser, when the user installed one;
    elsewhere it reaches nothing at all."""
    from ...runtime.isolation import own_loopback
    try:
        mine = own_loopback()
    except Exception:
        mine = False
    if not mine:
        return NETWORK_NONE_AT_ALL
    return NETWORK_OWN_LOOPBACK + ("\n\n" + NETWORK_BROWSER if browser else "")


AGENT_SYSTEM = """You are working on a piece of the user's work - a project -
for them. You work from THIS PROJECT alone: its goal and what the user asked,
its record so far, its files and folders, and the web.

YOU MAY OPEN THE REFERENCES. Every file and folder the project's record or
your tree points to - and any file or folder under the user's home - can be
listed and read with your tools, and so can web pages. Read them to help you
work: look at the actual files before you decide, and model what you make on
what is there.

THE WORK IS A TREE OF CARDS:
  - the ROOT is the goal - fixed by the contract, and not changed;
  - under it the DELIVERABLES - pieces of the result the user would name;
  - under those the PARTS - each one output with one check.
A card says what is true when it is done. Each open card carries its
CRITERIA - what it must meet, written by the reviewer for the user - and
APPROACH NOTES: how the user would go about it. Meet the criteria; follow
the notes.

YOU WORK IN ROUNDS. A round takes one card as far as it can go and ends
with what is now true - each claim with evidence that can be opened or
re-run. A reviewer then checks the evidence, not your reasoning. Never claim
a result you did not check.

{environment}

PYTHON PACKAGES can be installed with install_packages - wheels from PyPI
only, into this run, for run_python to import. {packages}"""

DRAFT_CONTRACT_TEXT = """DRAFT THE CONTRACT for this work, before any of it is done.

Read what you need first - the project's folders, its record. Then return
ONLY this JSON object:
{"goal": "what is true when the whole work is done - one or two sentences",
 "scope": ["what this work covers"],
 "out_of_scope": ["what it leaves alone"],
 "core_promises": [{"id": "P1",
                    "promise": "what the user gets - a behaviour they would try themselves",
                    "from_words": "the user's own words it comes from",
                    "real_condition": "how it is tried for real: as the user would use it, with real data, services and limits",
                    "fails_if": ["a concrete way it could fail that it must survive"]}],
 "deliverables": [{"ref": "d1", "title": "...",
                   "type": "code|document|slides|figure|email|message|data|form|meeting|setup|other",
                   "statement": "what is true when it is done",
                   "why": "what the goal needs it for",
                   "tier": "core|supporting|polish",
                   "promises": ["P1"]}]}

CORE_PROMISES_RULES

Once approved the goal is locked. Deliverables can be added later, by an
amendment the reviewer approves - so plan the near ones in detail and leave
the far ones rough rather than guessing."""

DRAFT_CONTRACT = DRAFT_CONTRACT_TEXT.replace("CORE_PROMISES_RULES", CORE_PROMISES_RULES)

REVISE_CONTRACT = """THE REVIEWER SENT THE CONTRACT BACK:
{review}

Revise it: return the whole contract JSON again."""

AMEND_CONTRACT = """AMEND THE CONTRACT FOR THIS BRANCH. The user has continued the work
with new words - THEIR INSTRUCTION, VERBATIM:
<<<
{words}
>>>

THE CONTRACT AS IT STANDS ("ref" of a deliverable is its card's id):
{contract}
{done}
Read what you need first - where the work stands, its files. Then return
ONLY the amended contract, in the same shape:
{{"goal": "...", "scope": ["..."], "out_of_scope": ["..."],
 "core_promises": [{{"id": "P1", "promise": "...", "from_words": "...",
                    "real_condition": "...", "fails_if": ["..."]}}],
 "deliverables": [{{"ref": "the card id of a deliverable that stays; d1, d2... for a new one",
                   "title": "...", "type": "...", "statement": "what is true when it is done",
                   "why": "...", "tier": "core|supporting|polish", "promises": ["P1"]}}],
 "changed": ["each clause you changed, and which of the user's words call for it"]}}

  - AN AMENDMENT, NOT A REWRITE: every clause the instruction does not touch
    is copied WORD FOR WORD - the goal, each line of scope, each core
    promise, each deliverable's title and statement. Change only what the
    words change; a new requirement in them is a new core promise.
  - A deliverable left out is dropped from this branch: leave one out only
    when the words say so.
  - The user's words are kept in the contract as they said them, by the
    application: do not copy or paraphrase them into a clause."""

REVIEW_AMENDMENT = """REVIEW THIS AMENDMENT OF THE CONTRACT. The user has continued the
work with new words - their instruction, verbatim:
<<<
{words}
>>>

THE CONTRACT BEFORE:
{before}

THE AMENDED CONTRACT, as the work agent drafted it:
{after}

WHAT CHANGED, clause by clause ("-" before, "+" after, the rest unchanged):
{diff}
{done}
Does the amendment follow the user's words - all of them, and only them? Did
it drop or change any clause the words do not touch? Write the ACCEPTANCE
CRITERIA and APPROACH NOTES of each deliverable that is new or whose
statement changed. Return ONLY this JSON object:
{{"verdict": "approve|revise",
  "comments": [{{"say": "...", "because": ["ids, or own judgement"]}}],
  "change": "for revise: what to change",
  "deliverables": [{{"ref": "...", "criteria": [{criteria}],
                     "approach": "..."}}],
  "reopen": [{{"card": "the id of a DONE card listed above",
              "why": "what of it no longer holds under the amended contract"}}]}}

  - "reopen": the done cards whose work no longer meets the amended
    contract - they are opened again. Leave out every one it does not touch.

{note}{last}"""

WORK_ROUND = """WORK ONE ROUND.

Take ONE card - an open deliverable or part - and move it as far as you can:
read, write, run, check. Meet its criteria; follow its approach notes.
CORE FIRST: while a core promise does not hold, take a core card - or the
supporting work a core card needs. A polish card waits until every core
promise holds; one taken before is sent back unreviewed. Then
stop calling tools and return ONLY this JSON object:
{"card": "the id of the card you worked on",
 "kind": "plan|execution|evaluation|adjustment|delivery",
 "objective": "the one thing this round set out to do",
 "result": "what you did and what came of it",
 "commit": {"title": "what this round changed, as a commit's title", "body": ["a concrete change"]},
 "claims": [{"claim": "something now true",
             "evidence": [{"path": "a full path you made or checked"},
                          {"command": "what you ran", "output": "what it printed"}]}],
 "open": ["what is still left on this card"],
 "decisions": [{"about": "...", "chose": "...", "over": "the alternative", "why": "..."}],
 "cards": [{"ref": "p1", "parent": "a deliverable's id", "title": "...", "type": "...",
            "statement": "what is true when it is done", "why": "...",
            "tier": "core|supporting|polish"}],
 "cards_changed": [{"card": "a part's id", "status": "set_aside", "why": "..."}],
 "amend": {"deliverables": [{"ref": "d9", "title": "...", "type": "...",
                             "statement": "...", "why": "..."}], "why": "..."},
 "blocked": {"card": "...", "missing": "what is missing - access, a tool, data - and why the card cannot move without it"},
 "complete": false}

  - Every claim needs evidence that can be opened or re-run.
  - "commit": what THIS round changed, said as a good commit message is - the
    version saved after it is named by it. "title": one line, at most 72
    characters, in the imperative ("Add the pledge model with two kinds and a
    per-user token ledger") - what changed, not what was attempted. "body":
    two to six short lines, each one concrete change (a file, a function, a
    result, a number).
  - "decisions": choices the user would have made themselves - the scope,
    which data, a trade-off between two things they care about - so they
    can check them afterwards.
  - "cards": parts under a deliverable. A NEW DELIVERABLE goes in "amend",
    for the reviewer to approve.
  - "blocked": only when the card cannot move at all without something you
    do not have.
  - "complete": true when you believe the whole goal is met."""

STEP_BACK = """TWO ROUNDS HAVE NOT MOVED THE WORK. Step back before you start:
re-read the contract and the open cards, say in "objective" what has been in
the way, and take a different approach - or a different card."""

FIX_FINDINGS = """THE FINAL REVIEW FOUND THESE - FIX THEM in this round (the card
is whichever each one is about), then return the round JSON:
{findings}"""

GATE = """THE ROUND SUMMARY FAILED THE CHECKS - nothing was reviewed:
{problems}

Fix what is needed - open, re-run or write it - and return the whole JSON
object again."""

PAUSE = """THE RUN IS PAUSING HERE: stop working now. Return the round JSON
for what you did so far - and in "open", what is left on this card and the
next thing you would do."""

MAKE_DECK = """MAKE THE RESULTS DECK: slides of what this work PRODUCED - not
how it went.{unfinished}

Open the deliverables first. Then return ONLY this JSON object:
{{"title": "...",
  "summary": "two or three sentences: what was produced",
  "deliverables": [{{"name": "...", "location": "its full path",
                     "what": "what it is and what it is for",
                     "card": "the id of its card"}}],
  "results": [{{"title": "...", "shows": "what this result shows",
                "figure": "the full path of an image of it, or empty",
                "table": {{"columns": ["..."], "rows": [["..."]]}},
                "variables": [{{"name": "...", "meaning": "what it means",
                                "value": "its value",
                                "source": "where it comes from: file and line, cell, or the command that printed it"}}]}}]}}

One results slide per KEY result. Its key variables are the numbers,
settings and names a reader needs to understand it - each explained. Put
nothing in it you did not see in the files."""

MAKE_PROGRESS_SLIDES = """MAKE THE PROGRESS SLIDES FOR VERSION {version} ({kind}: {label}).
{round_line}
A version is saved at each key point of the work, and each carries a short
PRESENTATION - like a good slide deck, shown to the user: WHERE THE WHOLE WORK
STANDS NOW, AT THIS VERSION - not only what is new - TOLD IN PICTURES, with
little text. Every slide but the title carries a visual: a chart of the work's
real numbers, a diagram of its structure or flow, an image the work produced,
big numbers, a timeline. The deck of the version before is below: build on it
- keep what still holds, update what changed - rather than starting over.

{work_note}DRAW THE FIGURES FIRST, with your tools:
  - YOUR SLIDES FOLDER, THE ONLY PLACE YOU CAN WRITE NOW: {folder}
    run_python starts there. Save each figure in it as a PNG or SVG file
    (fig_*.png) - a 16:9 figure (e.g. figsize=(12, 6.75), dpi=110) reads best.
    matplotlib and the like, if the run has them{packages}.
  - Charts of REAL NUMBERS from the work - read them from its files (results,
    logs, tables, data) - never made up. Diagrams of how its parts fit or
    flow. Images the work already produced can be shown as they are, by
    their full path in {work}.
  - The figures of {previous}'s deck: {previous_folder} - reuse what still
    holds (copy it into your folder, or name it), redraw what changed.
  - At most {rounds} rounds of tools: a few strong figures, not many.

WHAT THIS VERSION DID, AS ITS MESSAGE SAYS:
{change}

WHAT CHANGED IN THE WORK'S FILES SINCE {previous}:
{files}

THE ROUNDS SO FAR:
{rounds_done}

{findings}THE DECK OF {previous}:
{before}

THEN RETURN ONLY THIS JSON OBJECT - five to nine slides. The title slide
names it as the user knows it - {name} - and no other version or round number
for it:
{{"title": "a short title for where the work stands",
  "subtitle": "the state of the whole work in one sentence",
  "slides": [
    {{"layout": "title", "title": "...", "subtitle": "...", "notes": {{...}}}},
    {{"layout": "chart", "title": "what it shows, as a claim", "image": "the full path of a figure you drew", "caption": "a few words",
      "notes": {{"shows": "what this slide shows, in a sentence or two",
                "terms": [{{"term": "a word, abbreviation or variable on the slide", "means": "what it means here, plainly"}}],
                "read": "how to read it: the axes and units, what each colour or shape is, what to compare with what",
                "takeaway": "what to take from it"}}}},
    {{"layout": "metrics", "title": "...", "metrics": [{{"value": "83%", "label": "accuracy", "note": "on the held-out set"}}], "notes": {{...}}}},
    ...]}}

EVERY SLIDE HAS NOTES, shown beside it to the user, who reads the slide with
them and was not there when it was made: "shows" - what the slide shows;
"terms" - every key term, abbreviation, variable or name of a file or part
of the work on it, each with what it means here; "read" - how to read it:
the axes and their units, what each colour, line or shape stands for, what
to compare with what; "takeaway" - what to take from it. Plain sentences;
the slide stays short, the notes say the rest.

THE LAYOUTS - for each slide, the one that SHOWS its point best:
  title       title, subtitle - the first slide.
  chart       a chart you drew: image, caption.
  diagram     structure or flow: image (one you drew), OR "nodes": [{{"id", "label"}}]
              and "edges": [{{"from", "to", "label"}}] - drawn for you; caption.
  image       one image or figure: image, caption.
  image+text  a figure beside at most 4 short points: image, points, caption,
              "side": "left" or "right" (where the figure is).
  two-images  two figures side by side - before and after, this and that:
              "images": [{{"image", "caption"}}, {{"image", "caption"}}].
  comparison  two or three columns: "columns": [{{"heading", "image", "caption", "points"}}].
  timeline    dated milestones, done or not: "milestones": [{{"when", "what", "done"}}].
  metrics     two to four big numbers: "metrics": [{{"value", "label", "note"}}].
  bullets     ONLY when nothing visual fits: at most 5 short lines, "points".
Across the deck: what is done and what shows it, what is new since
{previous}, the results so far, what remains, the open problems, the next
steps. Titles say the point; captions and points are a few words, never
paragraphs. Nothing on them you did not see in the work. An image must be a
file inside {work} - any other is left out."""

#: A VERSION'S SLIDES ARE DRAWN FROM A COPY OF ITS FILES: no larger than
#: this, or they are drawn from the work's folder as it is.
VERSION_COPY_MOST_BYTES = 300 * 1024 * 1024

VERSION_COPY_NOTE = """THE WORK AS IT WAS AT {version}: a copy of its files is in {work}. Read the
work there - its results, logs, data - and draw from it: the work's folder,
{live}, is already the next step's. A file too large for a version to keep
is only there. To run the work's own code - to show what it does - run it
from that copy: what it writes as it runs (a database, a folder of its own)
is written there and thrown away with it. Figures still go in your slides
folder.
{before_note}
"""

#: THE VERSION BEFORE, TO COMPARE WITH: its files, to read - never to change.
BEFORE_COPY_NOTE = """THE WORK AS IT WAS AT {previous}, the version before: a copy of its files is
in {before} - READ them to compare with this version: its results, logs and
data then and now. You may only read there: nothing in it can be changed -
code that writes there is refused. These two copies are the only ones: no
other version's files are kept to read (a folder named for another version
is not there).
"""


FIX_JSON = """Your answer is not valid JSON - {problem}. Answer again with the same
JSON object, corrected - every brace and bracket closed once, every string
closed - and nothing else."""

MAKE_TEXT_SLIDES = """MAKE THE PROGRESS SLIDES FOR VERSION {version} ({kind}: {label}) - IN WORDS.
The slides of this version could not be drawn ({why}), so they are made in
words this time. A version is saved at each key point of the work, and each
carries a short deck: WHERE THE WHOLE WORK STANDS NOW, AT THIS VERSION - not
only what is new. The deck of the version before is below: build on it -
keep what still holds, correct what changed - rather than starting over. Use
only what you know from the work, the files and the record above; you cannot
open anything now.

WHAT THIS VERSION DID, AS ITS MESSAGE SAYS:
{change}

WHAT CHANGED IN THE WORK'S FILES SINCE {previous}:
{files}

THE ROUNDS SO FAR:
{rounds_done}

{findings}THE DECK OF {previous}:
{before}

Return ONLY this JSON object - three to six slides' worth, plainly said:
{{"title": "a short title for where the work stands",
  "summary": "two or three sentences: the state of the whole work at this version",
  "done": [{{"what": "a part of the work that is done", "evidence": "the file, result or check that shows it"}}],
  "new": ["what is new since {previous}, in a line each"],
  "results": [{{"title": "...", "shows": "what this result shows so far",
                "figure": "the full path of an image of it, or empty",
                "table": {{"columns": ["..."], "rows": [["..."]]}},
                "variables": [{{"name": "...", "meaning": "...", "value": "...",
                                "source": "where it comes from"}}]}}],
  "remaining": ["what is still to do for the contract"],
  "problems": ["an open problem or risk, and what it affects"],
  "next": ["the next step"]}}"""

GATE_DECK = """THE DECK FAILED THE CHECKS:
{problems}

Return the whole deck JSON again, fixed."""

REVISE_DECK = """THE REVIEWER SENT THE DECK BACK:
{review}

Return the whole deck JSON again, revised."""

REVIEWER_SYSTEM = """You review a work agent's work ON THE USER'S BEHALF. You
know how the user works: you are shown the past stages of THEIR OWN work most
like what is in hand - what they decided, what they required, what they
accepted and rejected - each with why it was found, and the rules those
stages show: their natures (how they judge, what they want the output to
be), and the patterns and principles above them. Judge as the user would.

  - EVERYTHING YOU SAY CITES WHAT IT RESTS ON: the id of a past stage,
    nature, pattern or principle you were shown ("because": ["stage_...",
    "rec_..."]), or "own judgement" when it rests on none of them.
  - Be specific: what is wrong, and what it should become.
  - WEIGH A RULE BY WHEN IT WAS LAST ACTIVE: each pattern and principle
    says when it was last seen in the user's work ("last_active"). One not
    seen for a long time may be a way they no longer work - lean on what is
    recent, and say so when an old rule is all there is.
  - AND BY HOW SURE IT IS: a nature says "how_sure" - "follow" (borne out by
    several stages of their work) or "lean on it" (still forming, or
    contradicted at times). Cite the natures a criterion rests on by their
    ids.
  - CHECK, DO NOT TRUST: open the files and the evidence. Judge what was
    made, not the reasons given for it.
  - THE RULES ARE HOW THE USER JUDGES, NOT MORE TO BUILD: use a principle,
    pattern or nature to judge whether the work gives the user what they
    asked, the way they would want it - never to add work they did not ask
    for. What they asked comes first: the core promises. A rule that bears on
    none of them is polish at most.
  - WHAT THE WORK AGENT READS CARRIES NO IDS: criteria and approach notes
    are plain requirements and instructions. The user's past work is yours
    to judge by; the work agent never sees it.
  - You decide. The user is not asked.

You can open cards, find more past stages, read files - and run code
(run_python) to try what was built, as the user would. Trying it cannot change the work: the work's folder and
every folder it was granted are read-only to you, and the code you run can
write only in your checks folder, {checks}. When the work must write as it
runs (a database, a cache), copy what it needs there and run it there."""

CRITERIA_SHAPE = """{"text": "a plain requirement the work agent can meet and you can check",
                 "because": ["ids, or own judgement"],
                 "backed_by": "check|experience|own judgement",
                 "serves": "the core promise it serves (P1...), or polish"}"""

CRITERIA_NOTE = """Criteria come from the user's work shown above: the stages most like
this (what they decided, required, accepted, rejected), the natures reached
through them - how they judge, and what they want the output to be (format,
content, style) - and the patterns and principles above. "backed_by": check
- something that can be run or opened settles it; experience - the user's
past work says so; own judgement - neither. Approach notes say how the user
would go about it, as plain instructions with no ids."""

REVIEW_CONTRACT = """REVIEW THIS CONTRACT, drafted by the work agent before any work:
{contract}

Is the goal what the user asked? Is the scope right, and are these the
deliverables they would name? ARE THE CORE PROMISES EVERY REQUIREMENT IN THE
USER'S WORDS - none missing, none invented - each tried under a real
condition, with the failures that matter to the user (money, limits, errors
from other services, many users at once)? Is each deliverable's tier right?
Send it back if not. Write each deliverable's ACCEPTANCE CRITERIA and
APPROACH NOTES - each criterion serving a core promise; one that serves none
is polish, and says so. Return ONLY this JSON object:
{{"verdict": "approve|revise",
  "comments": [{{"say": "...", "because": ["ids, or own judgement"]}}],
  "change": "for revise: what to change",
  "deliverables": [{{"ref": "d1", "criteria": [{criteria}],
                     "approach": "..."}}],
  "for_user": [{{"do": "what the user must check or do", "why": "why the work cannot do it itself, and what you tried instead", "promise": "P1, or empty"}}]}}

{note}

{for_user}{last}"""

REVIEW_CARD = """A CARD HAS OPENED - WRITE ITS CRITERIA:
{card}

Return ONLY this JSON object:
{{"criteria": [{criteria}], "approach": "..."}}

{note}"""

REVIEW_ROUND = """REVIEW THIS ROUND. The work agent's summary - what it claims, with
its evidence:
{summary}
{full}
FIRST, THE CORE PROMISES in the contract: try each one the way the user
would - under its real condition and its failure cases. Run it: start what
was built and use it, with real data and the real services where the work
has them. Reading the code or the agent's claims is not trying it. Then open
the evidence and check the claims against the card's criteria and the
contract. Return ONLY this JSON object:
{{"promises": [{{"id": "P1", "holds": "yes|no|untried|needs_user",
                 "tried": "what you ran, as the user would",
                 "failed": "what went wrong, when it did",
                 "user_does": "for needs_user: what the user must do to check it"}}],
  "for_user": [{{"do": "what the user must check or do", "why": "why the work cannot do it itself, and what you tried instead", "promise": "P1, or empty"}}],
  "findings": [{{"say": "...", "type": "mainline|blocking|queued",
                 "because": ["ids, or own judgement"],
                 "blocks_because": "for blocking: why it stops the goal",
                 "card": {{"title": "...", "statement": "..."}}}}],
  "progress": "advanced|stalled|regressed",
  "card_done": false,
  "checked": ["what you opened or ran"],
  "decisions": [{{"n": 0, "support": "strong|some|none", "because": ["ids"]}}],
  "amendment": "approved|rejected|none",
  "blocked_confirmed": false,
  "complete": false{stop}}}

  - "mainline": a gap in this card's own work. "blocking": a separate
    problem that stops the goal - say why. "queued": real, but not for now -
    it becomes a card, with "card" saying what is true when it is done.
  - "decisions": for each decision the agent made for the user (by its
    place, n, in the list), how strongly the user's past work supports it.
  - "amendment": your verdict on the deliverables it asked to add, if any.
  - "blocked_confirmed": the card really cannot move without what is
    missing.
  - "promises": every core promise, each as you found it THIS review -
    "untried" only when it cannot be tried yet (nothing to run). A promise
    that does not hold is a "mainline" or "blocking" finding too.
    {needs_user}
  - {for_user}
  - "complete": the whole goal is met - every deliverable's criteria, and
    every core promise holds when you tried it."""

FULL_REVIEW = """
THIS IS A FULL REVIEW: audit EVERY criterion of every deliverable against
the files, not only this card. You may also end the run: "stop": true, when
it cannot reach the goal and more rounds would not change that.
"""

REVIEW_FINAL = """THE GOAL IS MET BY YOUR LAST REVIEW. Now review everything the
work changed, as a whole:
{files}

Open them. Look for defects - wrong, missing, inconsistent, or not as the
user would have it - across the whole, not only what the last round
touched. Return ONLY this JSON object:
{{"findings": [{{"say": "...", "severity": "high|medium|low",
                 "file": "...", "because": ["ids, or own judgement"]}}],
  "checked": ["what you opened"],
  "for_user": [{{"do": "what the user must check or do", "why": "why the work cannot do it itself, and what you tried instead", "promise": "P1, or empty"}}]}}
High and medium findings are fixed; give low ones only when worth noting.

{for_user}"""

#: THE BLACK-BOX TESTER: before the final review, the finished work used
#: as its user would use it - by a context that never saw how it was built
#: nor what its builder claims of it. Only what the user asked, the core
#: promises and where the work is.
TESTER_SYSTEM = """You TEST a piece of work the way its user would use it. You never saw
how it was built, and you are not told what its builder claims of it: only
what the user asked, the core promises it must keep, and where it is.

  - USE IT: start it, call it, open it - with realistic data, and with each
    failure case a promise names. Reading the code is not testing it.
  - Do not fix anything. Trying it cannot change the work: the work's folder and
    every folder it was granted are read-only to you, and the code you run can
    write only in your checks folder, {checks}. When the work must write as it
    runs (a database, a cache), copy what it needs there and run it there.
  - Report exactly what you saw: what you ran, and what it did.

You can read files and run code (run_python)."""

TEST_AS_USER = """THE USER ASKED (their words, verbatim):
{words}

THE CORE PROMISES - what the work must do:
{promises}

THE WORK IS HERE:
{files}

Try every promise as the user would, then return ONLY this JSON object:
{{"promises": [{{"id": "P1", "holds": "yes|no|untried|needs_user",
                 "tried": "what you ran, as the user would",
                 "failed": "what went wrong, when it did",
                 "user_does": "for needs_user: what the user must do to check it"}}],
  "findings": [{{"say": "...", "severity": "high|medium|low", "file": "..."}}],
  "for_user": [{{"do": "what the user must check or do", "why": "why the work cannot do it itself, and what you tried instead", "promise": "P1, or empty"}}]}}
A promise that does not hold is a high finding. {needs_user}

{for_user}"""

REVIEW_DECK = """CHECK THE RESULTS DECK:
{deck}

Open the files it points to: does each location hold what it says, and does
each value match its source? Are the results shown the way the user likes
results shown? Return ONLY this JSON object:
{{"verdict": "accepted|rejected",
  "comments": [{{"say": "...", "because": ["ids, or own judgement"]}}]}}"""

# -- the reviewer with no experience: the Experience debugger's second way ---------------
REVIEWER_SYSTEM_NO_CONTEXT = """You review a work agent's work ON THE USER'S BEHALF.
No record of the user's past work is available to you: judge by what the
user asked, their own words, the contract and the evidence - as a careful
reviewer would.

  - EVERYTHING YOU SAY SAYS WHAT IT RESTS ON: the task, the contract, a file
    you opened, or "own judgement".
  - Be specific: what is wrong, and what it should become.
  - CHECK, DO NOT TRUST: open the files and the evidence. Judge what was
    made, not the reasons given for it.
  - WHAT THE WORK AGENT READS: criteria and approach notes are plain
    requirements and instructions.
  - You decide. The user is not asked.

You can open the cards of this work's tree, read files - and run code
(run_python) to try what was built, as the user would. Trying it cannot change the work: the work's folder and
every folder it was granted are read-only to you, and the code you run can
write only in your checks folder, {checks}. When the work must write as it
runs (a database, a cache), copy what it needs there and run it there."""

CRITERIA_NOTE_NO_CONTEXT = """Criteria come from what the user asked, their own words and the
contract. "backed_by": check - something that can be run or opened settles
it; own judgement - otherwise. Approach notes say how to go about it, as
plain instructions."""

#: What the reviewer's questions say of the user's experience, and what they
#: say instead to a reviewer that has none - so its prompts are the same
#: questions, asked without it.
NO_CONTEXT_WORDS = (
    (CRITERIA_NOTE, CRITERIA_NOTE_NO_CONTEXT),
    ('"check|experience|own judgement"', '"check|own judgement"'),
    ('"ids, or own judgement"', '"the task, the contract, a file, or own judgement"'),
    ('"because": ["ids"]', '"because": ["the task, the contract, a file, or own judgement"]'),
    ("how strongly the user's past work supports it",
     "how strongly the task, the contract and the evidence support it"),
    ("Are the results shown the way the user likes\nresults shown?",
     "Are the results shown clearly?"),
)

# -- one model on its own: the Experience debugger's first way ----------------------------
ALONE_SYSTEM = """You are doing a piece of work for the user, on your own: there
is no reviewer and no plan you must follow - how you go about it is yours to
decide.

YOU MAY OPEN THE REFERENCES. Every file and folder the task points to - and
any file or folder under the user's home - can be listed and read with your
tools, and so can web pages. Look at the actual files before you decide.

{environment}

PYTHON PACKAGES can be installed with install_packages - wheels from PyPI
only, into this run, for run_python to import. {packages}

WORK UNTIL THE WHOLE TASK IS DONE, with your tools, as long as you need.
Whenever you stop calling tools, answer with ONLY this JSON object:
{"done": false,
 "report": "what you did and what came of it: each result, where it is, and what it shows",
 "commit": {"title": "what this turn changed, as a commit's title", "body": ["a concrete change"]},
 "next": "when it is not done: what you will do next"}
"done": true only when the whole task is done - then the report is your
final report to the user. "commit" names the version saved after the turn,
as a good commit message does: "title" one line of at most 72 characters in
the imperative ("Add the parser for the survey's free-text answers") - what
changed, not what was attempted; "body" two to six short lines, each one
concrete change."""

ALONE_GO_ON = """GO ON with the work. When the whole task is done, say so -
"done": true, with your final report."""

ALONE_PAUSE = """THE RUN IS PAUSING HERE: stop working now. Return the JSON object
for what you did so far - "done": false, and in "next", what you would do
next."""

ALONE_RESUMED = """THIS WORK WAS BEGUN BEFORE, and is continued now. Your files in
{folder} are as you left them - look at them before you go on.
WHAT YOU REPORTED LAST:
{last}
{next}"""

ALONE_RESTARTED = """YOUR CONVERSATION GREW TOO LONG, and was started again here.
Your files are as you left them. WHAT YOU REPORTED, TURN BY TURN:
{reports}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", name.strip().lower(), flags=re.UNICODE)
    return re.sub(r"_+", "_", slug).strip("_")[:60] or "work"


def _as_json(text: str) -> Dict[str, Any]:
    text = text or ""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text[text.find("{"):text.rfind("}") + 1]
    try:
        out = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def json_problem(text: str) -> str:
    """What is wrong with the JSON object an answer gives - "" when it reads,
    or when there is no object in it at all: where it fails, in a line."""
    text = text or ""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text[text.find("{"):text.rfind("}") + 1]
    if not raw:
        return ""
    try:
        json.loads(raw)
    except ValueError as exc:
        line = getattr(exc, "lineno", None)
        column = getattr(exc, "colno", None)
        where = f"line {line}, column {column}: " if line else ""
        return f"{where}{getattr(exc, 'msg', str(exc))}"[:200]
    return ""


def _plain(text: Any) -> str:
    """Text for the work agent: no id of the user's experience in it."""
    return EXPERIENCE_ID.sub("", str(text or "")).replace("()", "").strip()


def limits_of(said: Any) -> Dict[str, int]:
    """The limits a run is started with: the user's as they gave them - zero
    or less meaning none on that one - and the defaults for what they left
    out."""
    said = said if isinstance(said, dict) else {}

    def given(name: str) -> Optional[int]:
        value = said.get(name)
        try:
            return None if value in (None, "") else max(0, int(float(value)))
        except (TypeError, ValueError):
            return None

    # ONE LIMIT ON TOKENS, as runs were limited before the two were apart:
    # kept as it was given, and then nothing limits what went in or came
    # out alone that was not given too - it was the user's whole say.
    total = given(TOTAL_TOKENS)
    alone = total is not None and given("tokens_in") is None and given("tokens_out") is None
    out = {}
    for name, default in DEFAULT_LIMITS.items():
        value = given(name)
        out[name] = value if value is not None else (
            0 if alone and name in ("tokens_in", "tokens_out") else default)
    if total:
        out[TOTAL_TOKENS] = total
    return out


def wants_sound(said: Any) -> bool:
    """Whether a sound was asked for: only a plain yes - true, 1, "yes",
    "on" - is one."""
    if isinstance(said, str):
        return said.strip().lower() in ("1", "true", "yes", "on")
    return said is True or (isinstance(said, int) and said == 1)


def calls_after(plan: Dict[str, Any], after: int = 0) -> List[Dict[str, Any]]:
    """A run's model calls as its plan keeps them (see `CALLS_KEPT`), those
    numbered after `after` - at most `CALLS_BATCH`, the latest."""
    calls = [one for one in (plan or {}).get("calls") or []
             if isinstance(one, dict) and int(one.get("n") or 0) > int(after or 0)]
    return calls[-CALLS_BATCH:]


def limit_reached(limits: Dict[str, Any], spent: Dict[str, float],
                  reserve: bool) -> str:
    """The limit that has been reached - its name, as `LIMIT_NAMES` - or "".
    `spent` says what has been spent of each; with `reserve`, a limit is
    reached while the wrap-up's share of it is still left. A limit of zero,
    or not there, is none."""
    for name in ("minutes", "tokens_in", "tokens_out", TOTAL_TOKENS, "calls"):
        try:
            limit = int((limits or {}).get(name) or 0)
        except (TypeError, ValueError):
            limit = 0
        if not limit:
            continue
        cap = limit - (reserve_of(name, limit) if reserve else 0)
        if spent.get(name, 0) >= cap:
            return name
    return ""


def reserve_of(name: str, limit: int) -> float:
    """What of one limit is kept for the wrap-up."""
    if not limit:
        return 0
    return min(max(RESERVE_FLOOR[name], RESERVE_SHARE * limit), limit / 2)


def _where(path: Any) -> str:
    """A path as one spelling: resolved, with forward slashes."""
    try:
        return Path(str(path)).resolve().as_posix()
    except (OSError, ValueError):
        return str(path).replace("\\", "/")


def promises_of(said: Any) -> List[Dict[str, Any]]:
    """The core promises a contract was drafted with, cleaned: each with an
    id (P1, P2... when it gave none), the promise, the user's words it
    comes from, how it is tried for real and what it must survive."""
    out: List[Dict[str, Any]] = []
    for one in said if isinstance(said, list) else []:
        if not isinstance(one, dict) or not str(one.get("promise") or "").strip():
            continue
        ident = str(one.get("id") or "").strip()[:12] or f"P{len(out) + 1}"
        if any(ident == known["id"] for known in out):
            ident = f"P{len(out) + 1}"
        out.append({"id": ident,
                    "promise": " ".join(str(one["promise"]).split())[:600],
                    "from_words": str(one.get("from_words") or "")[:600],
                    "real_condition": str(one.get("real_condition") or "")[:800],
                    "fails_if": [str(f)[:300] for f in one.get("fails_if") or []
                                 if str(f).strip()][:6]})
    return out[:12]


def tier_of(said: Any) -> str:
    """A tier as one of the three, "supporting" when it says none."""
    said = str(said or "").strip().lower()
    return said if said in TIERS else "supporting"


def contract_lines(contract: Dict[str, Any]) -> List[str]:
    """The contract as its clauses, a line each - what an amendment is
    compared by: the goal, each line of scope, each deliverable, and the
    user's words for the branch."""
    def one_line(text: Any) -> str:
        return " ".join(str(text if text is not None else "").split())

    contract = contract if isinstance(contract, dict) else {}
    lines = [f"GOAL: {one_line(contract.get('goal'))}"] if contract.get("goal") else []
    lines += [f"IN SCOPE: {one_line(one)}" for one in contract.get("scope") or []]
    lines += [f"OUT OF SCOPE: {one_line(one)}" for one in contract.get("out_of_scope") or []]
    lines += [f"CORE PROMISE {one.get('id', '')}: {one_line(one.get('promise'))}"
              for one in contract.get("promises") or [] if isinstance(one, dict)]
    lines += [f"DELIVERABLE: {one_line(one.get('title'))} - {one_line(one.get('statement'))}"
              for one in contract.get("deliverables") or [] if isinstance(one, dict)]
    lines += [f"THE USER'S INSTRUCTION FOR THIS BRANCH: {one_line(one)}"
              for one in contract.get("instructions") or []]
    return lines


def contract_diff(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    """What an amendment changed, clause by clause: every clause, "- " for
    one taken out, "+ " for one put in, two spaces for one unchanged."""
    return "\n".join(line for line in difflib.ndiff(contract_lines(before),
                                                     contract_lines(after))
                     if not line.startswith("? "))


def contract_shown(contract: Dict[str, Any], cards: Dict[str, Dict[str, Any]],
                   root_id: str = "", plan: Optional[Dict[str, Any]] = None
                   ) -> Optional[Dict[str, Any]]:
    """A contract as the page shows it: its goal, scope and the user's
    words for the line, and each deliverable with where its card stands and
    what it must meet - the criteria and approach notes the work agent is
    given, each criterion with what backs it. `cards` by id; with `root_id`,
    the deliverables an approved amendment of a round added are listed too.
    None for no contract yet. With the run's `plan`: each core promise as
    the reviewer or the tester last found it, each deliverable's tier, and
    what only the user can check or do."""
    contract = contract if isinstance(contract, dict) else {}
    if not contract.get("goal"):
        return None
    plan = plan if isinstance(plan, dict) else {}
    tiers = plan.get("tiers") if isinstance(plan.get("tiers"), dict) else {}
    found = plan.get("promise_status") if isinstance(plan.get("promise_status"), dict) else {}

    def shown(card: Optional[Dict[str, Any]], said: Dict[str, Any]) -> Dict[str, Any]:
        card = card or {}
        checked = card.get("checked_by") if isinstance(card.get("checked_by"), dict) else {}
        tier = tiers.get(card.get("id") or said.get("card", "")) or {}
        return {"card": card.get("id") or said.get("card", ""),
                "tier": tier.get("tier", ""), "serves": list(tier.get("promises") or []),
                "title": card.get("title") or said.get("title", ""),
                "statement": card.get("statement") or said.get("statement", ""),
                "status": card.get("status") or "",
                "criteria": [{"text": _plain(one.get("text")),
                              "backed_by": one.get("backed_by", "")}
                             for one in checked.get("criteria") or []
                             if isinstance(one, dict)],
                "approach": _plain(checked.get("approach") or "")}

    listed = [shown(cards.get(str(one.get("card") or "")), one)
              for one in contract.get("deliverables") or [] if isinstance(one, dict)]
    if root_id:
        seen = {one["card"] for one in listed}
        listed += [shown(card, {}) for card in cards.values()
                   if card.get("parent_id") == root_id and card["id"] not in seen
                   and card.get("status") != "set_aside"]
    promises = [dict(one, **{key: (found.get(one.get("id")) or {}).get(key, "")
                             for key in ("holds", "tried", "failed", "user_does", "round")})
                for one in contract.get("promises") or [] if isinstance(one, dict)]
    return {"goal": contract.get("goal", ""), "scope": list(contract.get("scope") or []),
            "promises": promises, "for_user": list(plan.get("for_user") or []),
            "out_of_scope": list(contract.get("out_of_scope") or []),
            "instructions": list(contract.get("instructions") or []),
            "deliverables": listed,
            "done": sum(1 for one in listed if one["status"] == "done"),
            "total": len(listed)}


def in_force_since(index: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The version the contract in force was set at: going back from the
    head along its line, the first saved when the contract was approved or
    amended."""
    by_id = {one["id"]: one for one in index.get("versions") or []}
    here = by_id.get(index.get("head") or "")
    while here is not None:
        if here.get("kind") in ("contract", "amended"):
            return here
        here = by_id.get(here.get("parent") or "")
    return None


def _criteria_of(said: Any) -> List[Dict[str, Any]]:
    out = []
    for one in said if isinstance(said, list) else []:
        if isinstance(one, str):
            one = {"text": one}
        if not isinstance(one, dict) or not str(one.get("text") or "").strip():
            continue
        backed = str(one.get("backed_by") or "own judgement").lower()
        serves = str(one.get("serves") or "").strip()[:12]
        out.append({"text": str(one["text"])[:600],
                    **({"serves": "polish" if serves.lower() == "polish" else serves}
                       if serves else {}),
                    "because": [str(b) for b in (one.get("because") or [])][:8]
                    if isinstance(one.get("because"), list) else
                    [str(one.get("because"))] if one.get("because") else [],
                    "backed_by": backed if backed in ("check", "experience",
                                                      "own judgement") else "own judgement"})
    return out[:12]


# -- the live session, in short -----------------------------------------------------
#: How long a version's title may be, as a commit's first line.
TITLE_CHARS = 72


def commit_title(text: Any) -> str:
    """A version's title, as a commit's first line: one line, its first
    letter upper case, no full stop at the end, at most `TITLE_CHARS` -
    cut at a word where it is too long."""
    line = " ".join(str(text or "").strip().splitlines()[0].split()) \
        if str(text or "").strip() else ""
    line = line.rstrip(" .;:,")
    if len(line) > TITLE_CHARS:
        cut = line[:TITLE_CHARS - 1]
        if " " in cut[TITLE_CHARS // 2:]:
            cut = cut[:cut.rindex(" ")]
        line = cut.rstrip(" ,;:-") + "…"
    return line[:1].upper() + line[1:]


def commit_message(said: Any, title: str, body: Any = ()) -> Dict[str, Any]:
    """A version's message: the commit the agent wrote in its answer
    (`said["commit"]`: a title and a few lines of what changed) - or, when
    it wrote none, `title` and `body`, said from what is known."""
    commit = said.get("commit") if isinstance(said, dict) else None
    if isinstance(commit, dict) and str(commit.get("title") or "").strip():
        title, body = commit.get("title"), commit.get("body")
    if isinstance(body, str):
        body = [one.strip().lstrip("-*• ").strip() for one in body.splitlines()]
    lines = [_clip(" ".join(str(one).split()), 160) for one in (body or [])
             if isinstance(one, (str, int, float)) and str(one).strip()]
    return {"title": commit_title(title), "body": lines[:6]}


def _clip(text: Any, most: int = 120) -> str:
    """One line of at most `most` characters - for a terminal-like view,
    where a line that wraps or runs on hides the next one."""
    line = " ".join(str(text if text is not None else "").split())
    return line if len(line) <= most else line[:most - 1] + "…"


#: The argument that says what a tool call is about - the rest is detail.
_KEY_ARGUMENT = {"list_dir": "path", "read_file": "path", "write_file": "path",
                 "delete_file": "path", "find_files": "pattern", "run_python": "code",
                 "web_search": "query", "fetch_url": "url", "open_card": "ids",
                 "search_stages": "query", "install_packages": "packages"}


def _short_args(name: str, arguments: Any) -> str:
    arguments = arguments if isinstance(arguments, dict) else {}
    key = _KEY_ARGUMENT.get(name)
    value = arguments.get(key) if key else None
    if value is None:
        value = next(iter(arguments.values()), "") if arguments else ""
    if isinstance(value, list):
        value = ", ".join(str(one) for one in value)
    if name == "run_python":
        # The first line of code that says something: the rest is in the log.
        lines = [one for one in str(value).splitlines() if one.strip()
                 and not one.strip().startswith(("import ", "from ", "#"))]
        value = lines[0] if lines else str(value)
    if name == "find_files" and arguments.get("root"):
        value = f"{value} in {arguments['root']}"
    return _clip(value, 90)


def _short_result(name: str, result: Any) -> Tuple[str, bool]:
    """What came of a tool call, in a few words - and whether it went."""
    if not isinstance(result, dict):
        return _clip(result, 90), True
    if result.get("error"):
        return _clip(f"{result.get('error')}: {result.get('message') or ''}".rstrip(": "),
                     110), False
    if name == "write_file":
        return "written", True
    if name == "delete_file":
        return "deleted", True
    if name == "read_file":
        return f"{len(str(result.get('text') or ''))} characters", True
    if name == "list_dir":
        entries = result.get("entries") or result.get("items") or []
        return f"{len(entries)} entries" if isinstance(entries, list) else "listed", True
    if name == "run_python" and result.get("job"):
        return f"started {result['job']} in the background", True
    if name in ("job_status", "stop_job") and result.get("job"):
        inner = result.get("result") or {}
        printed = [one for one in str(inner.get("stdout") or "").splitlines() if one.strip()]
        said = f"{result['job']}: {result.get('status')} after {result.get('seconds')}s"
        return (_clip(f"{said}{' - ' + printed[-1] if printed else ''}", 110),
                result.get("status") in ("running", "done", "stopped"))
    if name == "job_status" and isinstance(result.get("jobs"), list):
        return f"{len(result['jobs'])} jobs", True
    if name == "run_python":
        code = result.get("returncode", result.get("exit_code"))
        printed = [one for one in str(result.get("stdout") or "").splitlines() if one.strip()]
        said = f"exit {code}" if code is not None else "ran"
        if result.get("ended_with_the_code"):
            said += f" - ended {len(result['ended_with_the_code'])} program(s) it left running"
        return _clip(f"{said}{' - ' + printed[-1] if printed else ''}", 110), code in (0, None)
    if name == "install_packages":
        if result.get("approved") is False:
            return "not approved", False
        got = [f"{one.get('name')} {one.get('version')}" for one in
               result.get("installed") or [] if isinstance(one, dict)]
        return _clip("installed " + ", ".join(got) if got else "nothing installed", 110), True
    for key in ("results", "cards", "files", "examples", "matches"):
        if isinstance(result.get(key), list):
            return f"{len(result[key])} {key}", True
    if result.get("title"):
        return _clip(result["title"], 90), True
    return "done", True


def _whole(text: Any, most: int = 0) -> str:
    """The words with nothing cut - `_clip`'s other half."""
    return str(text if text is not None else "")


def _tool_in_full(name: str, arguments: Any, result: Any) -> str:
    """A tool call whole: every argument - code as it was written, newlines
    kept - and all it gave back."""
    arguments = arguments if isinstance(arguments, dict) else {}
    lines = [f"{name}"]
    for key, value in arguments.items():
        if isinstance(value, str) and ("\n" in value or len(value) > 80):
            lines.append(f"{key}:\n{value}")
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}")
    lines.append("→")
    if isinstance(result, dict):
        for key, value in result.items():
            if isinstance(value, str) and ("\n" in value or len(value) > 80):
                lines.append(f"{key}:\n{value}")
            else:
                lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}")
    else:
        lines.append(str(result))
    return "\n".join(lines)


def _gist(text: str) -> str:
    """An answer in a line: the field of its JSON that says most, or its
    first words."""
    said = _as_json(text)
    for key in ("verdict", "progress", "objective", "goal", "title", "summary", "result"):
        if said.get(key):
            extra = ""
            if isinstance(said.get("findings"), list):
                extra = f" ({len(said['findings'])} finding{'s' if len(said['findings']) != 1 else ''})"
            return _clip(f"{key}: {said[key]}{extra}", 150)
    if isinstance(said.get("criteria"), list):
        return f"{len(said['criteria'])} criteria"
    if isinstance(said.get("findings"), list):
        return f"{len(said['findings'])} findings"
    return _clip(text, 150) or "(nothing)"


def _note_as_event(entry: Dict[str, Any], whole: bool = False
                   ) -> Optional[Tuple[str, str, str, Dict[str, Any]]]:
    """A line of the run's log, as a line of the live session: who, what
    kind of moment, the words, and the detail the page needs. None for what
    the session shows another way. `whole`: the words with nothing cut -
    every comment, every finding, in full - kept beside the short line."""
    what = str(entry.get("what") or "")
    said = entry.get
    _clip = _whole if whole else globals()["_clip"]

    def joined(items: Any) -> str:
        if whole:
            return "; ".join(_whole(one) for one in list(items or []))
        return "; ".join(_clip(one, 120) for one in list(items or [])[:3])

    if what == "instruction":
        return "you", "start", f"You asked: {_clip(said('text'), 300)}", {}
    if what == "continued":
        return "run", "milestone", f"Continued - the {said('phase') or 'work'} goes on", {}
    if what == "contract reviewed":
        comments = joined(said("comments"))
        return ("reviewer", "verdict", f"The contract: {said('verdict') or 'reviewed'}"
                + (f" - {comments}" if comments else ""), {"verdict": said("verdict")})
    if what == "branch instruction":
        return ("you", "start", f"You asked, from here: {_clip(said('text'), 300)}",
                {"words": said("text")})
    if what == "amendment reviewed":
        comments = joined(said("comments"))
        return ("reviewer", "verdict", f"The amendment: {said('verdict') or 'reviewed'}"
                + (f" - {comments}" if comments else ""),
                {"verdict": said("verdict"), "reopen": said("reopen") or []})
    if what == "contract amended":
        reopened = said("reopened") or []
        return ("run", "contract", "Contract amended for this branch"
                + (f" - {len(reopened)} done card{'s' if len(reopened) != 1 else ''} "
                   f"opened again: {joined(reopened)}" if reopened else ""),
                {"diff": said("diff") or "", "words": said("words") or "",
                 "reopened": reopened})
    if what == "amendment not approved":
        return ("run", "contract", "The amendment was not approved: the contract stays "
                "as it was, and no work is done on it", {"ok": False})
    if what == "criteria written":
        return ("reviewer", "criteria",
                f"Wrote {said('count')} criteria for {_clip(said('card'), 80)}", {})
    if what == "checks failed":
        problems = said("problems") or []
        return ("run", "gate", f"Round {said('round')}: the summary failed the checks "
                f"({len(problems)}) - {joined(problems)}", {"ok": False,
                                                              "problems": problems[:8]})
    if what == "round reviewed":
        findings = said("findings") or []
        return ("reviewer", "verdict", f"Round {said('round')} ({_clip(said('card'), 60)}): "
                f"{said('progress') or 'reviewed'}"
                + (f" - {len(findings)} finding{'s' if len(findings) != 1 else ''}: "
                   f"{joined(findings)}" if findings else ""),
                {"verdict": said("progress"), "findings": findings[:6]})
    if what == "final review":
        return ("reviewer", "verdict", f"Final review: {said('findings')} findings, "
                f"{said('to_fix')} to fix", {"verdict": "final review"})
    if what == "deck made":
        return "worker", "milestone", f"The results deck is made ({said('slides')} slides)", {}
    if what == "version saved":
        return ("run", "version", f"Version {said('version')} saved - {_clip(said('label'), 160)}",
                {"version": said("version"), "branch": said("branch"),
                 "version_kind": said("kind")})
    if what == "slides drawn again":
        return ("run", "version", f"The slides of {said('version')} were not drawn when "
                "the session ended: they are drawn now, behind the work",
                {"version": said("version")})
    if what == "slides drawn":
        return ("worker", "version", f"The slides of {said('version')} are drawn",
                {"version": said("version")})
    if what == "slides not made":
        return ("run", "version", f"The slides of {said('version')} were not made: "
                f"{_clip(said('why'), 160)}", {"version": said("version")})
    if what == "version not saved":
        return "run", "error", f"A version could not be saved: {_clip(said('error'), 160)}", {}
    if what == "asked to install packages":
        packages = ", ".join(said("packages") or [])
        return ("worker", "permission", f"Asks to install {packages} - why: "
                f"{_clip(said('why'), 200)}", {"request": said("request"),
                                               "packages": said("packages") or [],
                                               "why": said("why")})
    if what in ("model needs you", "model limit reached", "model not answering"):
        resets = said("resets_at")
        when = ""
        if resets:
            try:
                when = f" - it resets at {_clock(datetime.fromisoformat(resets).timestamp())}"
            except ValueError:
                when = ""
        head = {"model limit reached": "The model's usage limit is reached",
                "model not answering": "The model has not answered for "
                                       f"{_seconds_said(TEMPORARY_CAP_SECONDS)}: no more tries "
                                       "by itself"}.get(what, "The model needs you")
        return ("run", "permission", f"{head}{when}: {_clip(said('reason'), 220)} - "
                "waiting for you", {"request": said("request")})
    if what.startswith("model wait "):
        answer = what[len("model wait "):]
        return ("you" if answer == ENDED else "run", "answer", {
            CONTINUED: "The model answers again - the run goes on",
            ENDED: "You ended the run while the model needed you",
            REPLACED: "The model failed another way",
            UNANSWERED: "The run was stopped while it waited for the model"}.get(
                answer, f"The wait for the model ended: {answer}"),
            {"request": said("request"), "status": answer})
    if what.startswith("package request "):
        answer = what[len("package request "):]
        who = "you" if answer in (APPROVED, DENIED) else "run"
        return (who, "answer", {APPROVED: "You approved the install",
                                DENIED: "You denied the install"}.get(
                                    answer, f"The install request ended: {answer}"),
                {"request": said("request"), "status": answer})
    if what in ("installed packages", "install failed"):
        got = ", ".join(said("installed") or []) or ", ".join(said("packages") or [])
        return ("run", "install", (f"Installed {got}" if what == "installed packages"
                                   else f"Install failed: {_clip(said('error'), 160)}")
                + f" ({said('mode')})", {"ok": what == "installed packages"})
    if what == "packages refused":
        return "run", "install", f"Packages refused: {joined(said('why'))}", {"ok": False}
    if what == "rolled back":
        return ("run", "version", f"Rolled back to {said('version')} - {_clip(said('label'), 120)}"
                f" ({said('branch')})", {"version": said("version")})
    if what == "ended":
        reason = said("reason")
        return ("run", "end", f"Ended: {said('status')}" + (f" - {reason}" if reason else ""),
                {"status": said("status")})
    if what == "failed":
        return "run", "error", f"Failed: {_clip(said('error'), 300)}", {}
    return "run", "note", _clip(what, 200), {}


def _log_stamp(started_at: str) -> str:
    """When a run started, in this computer's own time, as a name sorts:
    "2026-10-09_14-32-05". Now, for a time that cannot be read."""
    try:
        moment = datetime.fromisoformat(str(started_at or ""))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moment = moment.astimezone()
    except (TypeError, ValueError, OverflowError, OSError):
        moment = datetime.now().astimezone()
    return moment.strftime("%Y-%m-%d_%H-%M-%S")


def log_dir_for(folder: Path, run_id: str, started_at: str = "") -> Path:
    """WHERE A RUN'S LOGS ARE - its full transcript and its live session.

    In the project's one folder of logs, `run_logs/`, a folder per run
    named by when it started and then its id - "2026-10-09_14-32-05_run_ab12"
    - so the file manager's sort by name is the order the runs happened in.
    One already there is found by its id, whatever time it was named by; a
    new one is named from `started_at`.

    A run from before the logs had a folder of their own keeps them where
    they were, in `runs/<run>/.session/` - read there and, continued,
    written there: nothing is moved."""
    folder = Path(folder)
    old = folder / "runs" / run_id / SESSION_DIR
    if old.is_dir():
        return old
    logs = folder / LOGS_DIR
    try:
        found = sorted(one for one in logs.glob(f"*_{glob.escape(run_id)}")
                       if one.is_dir())
    except OSError:
        found = []
    if found:
        return found[0]
    return logs / f"{_log_stamp(started_at)}_{run_id}"


def open_in_file_manager(folder: Path) -> None:
    """A folder shown in the machine's own file manager: Explorer on
    Windows, Finder on a Mac, whatever opens folders elsewhere."""
    import subprocess
    import sys
    if sys.platform.startswith("win"):
        try:
            os.startfile(str(folder))        # type: ignore[attr-defined]
        except OSError:
            subprocess.Popen(["explorer", str(folder)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def read_session(log_dir: Path) -> List[Dict[str, Any]]:
    """The live session as kept in a run's folder of logs (see
    `log_dir_for`): its last events, in order. A line cut off when the
    application closed is skipped."""
    path = Path(log_dir) / SESSION_FILE
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            if size > SESSION_TAIL_BYTES:
                handle.seek(size - SESSION_TAIL_BYTES)
                handle.readline()               # the line the seek landed in
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            one = json.loads(line)
        except ValueError:
            continue
        if isinstance(one, dict) and isinstance(one.get("seq"), int):
            out.append(one)
    return out[-SESSION_EVENTS:]


def _exchanges_text(response: Any) -> str:
    """The tools a call ran itself (`ModelResponse.tool_exchanges`), as
    words to keep in the conversation - "" when it ran none."""
    ran = getattr(response, "tool_exchanges", None) or []
    if not ran:
        return ""
    lines = ["(The tools I ran during this answer, and what each returned:)"]
    for one in ran:
        lines.append(f"- {one.get('name', '')} "
                     f"{json.dumps(one.get('arguments') or {}, ensure_ascii=False)[:2000]}"
                     f" -> {str(one.get('result', ''))[:4000]}")
    return "\n".join(lines) + "\n\n"


class WorkRuns:
    """Starts runs, one at a time, continues them, and reports on them."""

    def __init__(self, db: Any, app_policy: WorkspacePolicy, models: Any,
                 experience: Any, platform: Any = None,
                 sandbox_enabled: bool = True, raw_store: Any = None) -> None:
        self.db = db
        self.app_policy = app_policy
        self.models = models
        self.experience = experience
        self.platform = platform
        self.sandbox_enabled = sandbox_enabled
        #: Where what the agents do is recorded, as the agent's.
        self.raw_store = raw_store
        self._running: Dict[str, "Run"] = {}
        #: EACH RUN IN A PROCESS OF ITS OWN (child.py) - set by the application;
        #: off, they go in this process (the tests, and the debugger's runs).
        self.in_subprocess = False
        self.paths: Any = None
        #: How much of the computer the runs may have (governor.py), and the
        #: runs asked for while there was no room, first asked first started
        #: - the user's own and the Experience debugger's ways alike.
        self.governor: Any = None
        #: The one pool everything side by side takes its places in
        #: (common/pool.py): a run starts when the governor AND the pool have
        #: room - the first always may, as the first of each kind does.
        self.pool: Any = None
        #: A debugger way that waited for room and then could not start:
        #: why, by its unit, until it is started or asked about again.
        self.waited_out: Dict[str, str] = {}
        self.waiting: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        #: What installs a run's packages, given the work's policy: pip under
        #: the guard. Replaced in tests.
        self.installer_for = lambda policy: Installer(
            policy, platform=self.platform, sandbox_enabled=self.sandbox_enabled)
        #: What shows a folder in the machine's own file manager. Replaced in
        #: tests - which never open one.
        self.open_folder = open_in_file_manager
        #: THE APPLICATION'S OWN CODE, which a way of the Experience debugger
        #: may not read - the user's decision: a way being measured does not
        #: learn how the system measuring it works. Real work reads it as
        #: before. Replaced in tests, with a folder of their own.
        from ...common.paths import app_code_root
        self.code_root: Optional[Path] = app_code_root()
        #: What plays the sound a session ends with, when the user asked for
        #: one: given how it ended - "done", "failed" or "stopped" (a limit
        #: reached, stopped by the user, or paused otherwise). Played by the
        #: application itself, so it is heard with the page closed. Replaced
        #: in tests - which never play one (PA_NO_SOUND, besides).
        self.play_sound = play_end_sound
        self._settle_interrupted()

    def _settle_interrupted(self) -> None:
        """A run that was going when the application last closed: marked
        interrupted, to be continued by the user - never by itself. A
        request to install packages it was waiting on ends as not approved:
        nobody answered it, and it is not asked again by itself either.

        A PAUSED one too: its place - the round, the conversation - was held
        in the application's memory, which is gone. It goes on from its last
        milestone instead (see `continue_run`)."""
        try:
            for row in self.db.query("SELECT id, plan FROM work_runs WHERE "
                                     "status IN ('running', ?, ?)", (WAITING, PAUSED)):
                try:
                    plan = json.loads(row["plan"] or "{}") or {}
                except ValueError:
                    continue
                if _settle_requests(plan, "the application closed while it waited"):
                    self.db.execute("UPDATE work_runs SET plan = ? WHERE id = ?",
                                    (json.dumps(plan, ensure_ascii=False, default=str),
                                     row["id"]))
            self.db.execute("UPDATE work_runs SET status = ?, ended_at = ?, "
                            "updated_at = ? WHERE status IN ('running', ?, ?)",
                            (INTERRUPTED, _now(), _now(), WAITING, PAUSED))
        except Exception:        # pragma: no cover - a database without the table
            log.debug("could not settle interrupted runs", exc_info=True)

    def folder_for(self, unit_name: str, unit_id: str) -> Path:
        root = Path(self.app_policy.workspace_root) / WORK_DIR
        return root / f"{_slug(unit_name)}-{unit_id[-6:]}"

    # -- the folders the user granted ------------------------------------------------
    #: Said whenever a folder is granted.
    GRANT_WARNING = ("Agents working on this project can create, change and "
                     "delete any file in this folder. Keep a backup of anything "
                     "in it you care about somewhere else.")

    def folders(self, unit_id: str) -> List[str]:
        return [row["path"] for row in self.db.query(
            "SELECT path FROM work_folders WHERE unit_id = ? ORDER BY added_at",
            (unit_id,))]

    def writable_folder(self, path: str) -> Dict[str, Any]:
        """Whether the agents may be let write in a folder the user chose:
        {"folder": the folder, resolved} - or {"error": why not}. Not a whole
        drive or the home folder itself, and never the application's own
        state. What a grant asks, and what a project of the Experience
        debugger asks of the folder its three ways work in."""
        folder = Path(str(path or "")).expanduser()
        if not str(path or "").strip() or not folder.is_dir():
            return {"error": f"not a folder: {path}"}
        folder = folder.resolve()
        if folder.parent == folder:
            return {"error": "a whole drive cannot be granted"}
        if folder == Path.home().resolve():
            return {"error": "the home folder itself cannot be granted - choose "
                             "a folder inside it"}
        for kept in self.app_policy.protected_paths:
            kept = Path(kept).resolve()
            if folder == kept or kept in folder.parents:
                return {"error": "that is inside the application's own state"}
        return {"folder": folder}

    def grant(self, unit_id: str, path: str) -> Dict[str, Any]:
        """Let the agents working on this project write in a folder. Not a
        whole drive or the home folder itself, and never the application's
        own state."""
        said = self.writable_folder(path)
        if said.get("error"):
            return said
        folder = said["folder"]
        self.db.execute("INSERT OR IGNORE INTO work_folders (unit_id, path, "
                        "added_at) VALUES (?, ?, ?)", (unit_id, str(folder), _now()))
        return {"folder": str(folder), "warning": self.GRANT_WARNING}

    def revoke(self, unit_id: str, path: str) -> bool:
        self.db.execute("DELETE FROM work_folders WHERE unit_id = ? AND path = ?",
                        (unit_id, str(path)))
        return True

    @staticmethod
    def closed_for(folder: Path) -> List[Path]:
        """THE RECORD OF WHAT HAPPENED in a project's folder, which its
        agents may neither read nor write (WorkspacePolicy.closed_paths):
        its run logs, and where runs from before kept their own."""
        folder = Path(folder)
        closed = [folder / LOGS_DIR]
        try:
            closed += sorted(one for one in (folder / "runs").glob(f"*/{SESSION_DIR}")
                             if one.is_dir())
        except OSError:
            pass
        return closed

    def policy_for(self, folder: Path, granted: List[str] = (),
                   sealed: List[str] = (),
                   code_root: Optional[Path] = None) -> WorkspacePolicy:
        """Read like the agent always does; write only in its own folder and
        the granted ones; no network from code it runs. `sealed`: folders it
        may not even read - the other ways' of a project of the Experience
        debugger. `code_root`: the application's own code, for those ways
        too - not read either (WorkspacePolicy.code_root)."""
        folder.mkdir(parents=True, exist_ok=True)
        return WorkspacePolicy(
            workspace_root=Path(self.app_policy.workspace_root),
            writable_root=folder,
            readable_paths=list(self.app_policy.readable_paths),
            readonly_paths=list(self.app_policy.readonly_paths),
            protected_paths=list(self.app_policy.protected_paths),
            extra_writable=[Path(one) for one in granted if Path(one).is_dir()],
            sealed_paths=[Path(one).resolve() for one in sealed],
            code_root=Path(code_root).resolve() if code_root else None,
            closed_paths=[one.resolve() for one in self.closed_for(folder)],
            network=False)

    def readable_by(self, run_id: str, path: str) -> Optional[Path]:
        """A file or folder of a run - in its folder or a folder it was
        granted - for the page to show; None for anything else."""
        rows = self.db.query("SELECT unit_id, folder FROM work_runs WHERE id = ?",
                             (run_id,))
        if not rows or not str(path or "").strip():
            return None
        roots = [Path(rows[0]["folder"]), *[Path(one) for one in
                                            self.folders(rows[0]["unit_id"])]]
        try:
            target = Path(path).resolve()
        except (OSError, ValueError):
            return None
        for root in roots:
            root = root.resolve()
            if (target == root or root in target.parents) and target.exists():
                return target
        return None

    # -- starting --------------------------------------------------------------------
    def has_history(self, unit: Dict[str, Any]) -> bool:
        """Whether the work has anything to carry on from: a record of the
        user's own, or a tree of earlier runs."""
        from ...heartbeat.experience.store import ExperienceStore
        if ExperienceStore(self.db, work_scope(unit["id"])).stages([unit["id"]]):
            return True
        if self.experience is None:
            return False
        full = self.experience.full_unit(unit["id"]) or unit
        from ...heartbeat.experience.stages import unit_task_ids
        return bool(self.experience.store.stages(unit_task_ids(
            dict(full, tasks=full.get("tasks") or []))))

    def _runtime(self, unit: Dict[str, Any], folder: Path) -> SandboxRuntime:
        # A way of the Experience debugger writes in its own subfolder of the
        # folder the user chose, AND NOWHERE ELSE: no folder is granted it -
        # and the other two ways' subfolders are sealed from it: it cannot
        # read them either - nor the application's own code.
        debug = unit.get("debug") if isinstance(unit.get("debug"), dict) else None
        granted = [] if debug else self.folders(unit["id"])
        sealed = list(debug.get("sealed") or []) if debug else []
        return SandboxRuntime(self.policy_for(folder, granted, sealed,
                                              self.code_root if debug else None),
                              platform=self.platform,
                              sandbox_enabled=self.sandbox_enabled)

    def folder_of(self, unit: Dict[str, Any]) -> Path:
        """Where a piece of work's runs write: its folder under agent_work -
        or, for a way of the Experience debugger, that way's own subfolder of
        the folder the user chose for it."""
        debug = unit.get("debug") if isinstance(unit.get("debug"), dict) else None
        if debug:
            return Path(debug["folder"])
        # Delegated work given no folder: the one in the workspace named after
        # it (delegate.py, `output_folder_for`).
        try:
            held = self.experience.store.workspace(unit["id"]) if self.experience else None
        except Exception:
            held = None
        output = ((held or {}).get("summary") or {}).get("output_folder")
        if output:
            return Path(output)
        return self.folder_for(unit["name"], unit["id"])

    def _busy(self, unit: Dict[str, Any]) -> str:
        """Why a run of this work cannot start now - "" when it can.

        THE USER'S OWN WORK: one run at a time, among their own runs. THE
        EXPERIENCE DEBUGGER'S: one run at a time of each way of each of its
        projects - its ways may run side by side, and beside the user's own,
        as the computer has room: each waits its turn as any run does
        (`_room_for`)."""
        if unit.get("debug"):
            if any(run.alive() and run.unit.get("id") == unit["id"]
                   for run in self._running.values()):
                return "this one is already going; stop it first"
            if any(one["unit"].get("id") == unit.get("id") for one in self.waiting):
                return "this one is already waiting to start"
            return ""
        if self.in_subprocess:
            # Each in a process of its own: several pieces of work may go at
            # once, as the computer has room (governor.py) - one run of each.
            if any(run.alive() and not getattr(run, "debug", False)
                   and run.unit.get("id") == unit.get("id")
                   for run in self._running.values()):
                return "this work is already going; stop it first"
            if any(one["unit"].get("id") == unit.get("id") for one in self.waiting):
                return "this work is already waiting to start"
            return ""
        if any(run.alive() and not getattr(run, "debug", False)
               for run in self._running.values()):
            return "a run is already going; stop it first"
        return ""

    # -- the application's code, sealed while a debugger run is alive -------------
    def hold_code(self, run: "Run") -> Optional[Path]:
        """A run of the Experience debugger starts: the application's code
        is sealed from the agents' code until it ends - on Windows, where
        the home's read grant is shared by every run (runtime/isolation.
        CodeSeal; elsewhere each of its children is kept out on its own).
        The folder held, to give back with `release_code`; None when there
        is nothing to hold: not a debugger run, or no operating system
        isolation to hold it with."""
        if not getattr(run, "debug", False) or not self.sandbox_enabled:
            return None
        root = getattr(run.runtime.policy, "child_code_root", None)
        if root is None:
            return None
        from ...runtime.isolation import CodeSeal
        CodeSeal.hold(root)
        return root

    @staticmethod
    def release_code(root: Optional[Path]) -> None:
        """A debugger run ended: the code opens again if it was the last."""
        if root is None:
            return
        from ...runtime.isolation import CodeSeal
        CodeSeal.release(root)

    def settle_code(self) -> None:
        """As the application starts, when no debugger run can be alive (an
        interrupted one waits for the user): the code open again, should a
        seal have been left by runs the application ended with. Off the
        caller's thread (runtime/isolation.CodeSeal)."""
        if self.code_root is None or not self.sandbox_enabled:
            return
        from ...common.paths import code_root_for_children
        if code_root_for_children() is None:
            return
        from ...runtime.isolation import CodeSeal
        CodeSeal.settle(self.code_root)

    def going_now(self) -> List[Dict[str, Any]]:
        """EVERY RUN GOING NOW, in brief - for the status readout, which is
        read on every hover: a run goes on when its panel is closed, the
        Experience debugger's included, and the readout is where the user
        sees that it does. Only what is in memory; nothing is read or asked."""
        out: List[Dict[str, Any]] = []
        for run in list(self._running.values()):
            try:
                if not run.alive():
                    continue
                debug = run.unit.get("debug") if isinstance(run.unit.get("debug"), dict) else {}
                used = dict(getattr(run, "used", {}) or {})
                status = "stopping" if getattr(run, "stopping", False) else str(run.status)
                out.append({
                    "id": run.id, "name": str(run.unit.get("name") or ""),
                    "way": str(debug.get("label") or ""), "debugger": bool(debug),
                    "status": status, "phase": str(run.plan.get("phase") or ""),
                    "round": run.plan.get("round"),
                    "minutes": round(run._minutes(), 1),
                    "tokens_in": int(used.get("tokens_in") or 0),
                    "tokens_out": int(used.get("tokens_out") or 0),
                    "calls": int(used.get("calls") or 0),
                    "waiting_for": run.waiting_for()})
            except Exception:        # pragma: no cover - a run mid-change
                log.debug("could not read a going run", exc_info=True)
        return out

    def going(self, debug: bool = False) -> bool:
        """Whether a run is going: of the user's own work, or - `debug` - of
        the Experience debugger's."""
        return any(run.alive() and bool(getattr(run, "debug", False)) == debug
                   for run in list(self._running.values()))

    def start(self, unit: Dict[str, Any], instruction: str = "",
              limits: Any = None, packages: Any = None,
              sound: Any = False, given_room: bool = False) -> Dict[str, Any]:
        """Start a run. `packages` is "ask" - the user approves each install,
        the default - or "auto". `sound`: a sound is played when this
        session ends (see `Run._ring`). `given_room`: started from the
        queue, its turn come (`start_waiting`)."""
        with self._lock:
            busy = self._busy(unit)
            if busy:
                return {"error": busy}
            if not instruction.strip() and not self.has_history(unit):
                return {"error": "this is new work: say what you want done"}
            if unit.get("debug") and not given_room:
                waits = self._wait_for_room("start", unit, {
                    "instruction": instruction, "limits": limits,
                    "packages": packages, "sound": sound})
                if waits:
                    return waits
            if self.in_subprocess and not unit.get("debug"):
                return self._apart("start", unit, instruction=instruction,
                                   limits=limits, packages=packages, sound=sound)
            folder = self.folder_of(unit)
            run = Run(self, unit, instruction, self._runtime(unit, folder),
                      limits_of(limits), packages=mode_of(packages),
                      sound=wants_sound(sound))
            self._running[run.id] = run
            run.start()
            return {"run": run.id, "folder": str(folder), "limits": run.limits,
                    "packages": run.packages_mode, "sound": run.sound}

    def continue_run(self, run_id: str, unit: Dict[str, Any],
                     limits: Any = None, packages: Any = None,
                     from_version: str = "", instruction: str = "",
                     sound: Any = False, given_room: bool = False) -> Dict[str, Any]:
        """Carry a run on from where it stopped, with new limits - and, if
        `packages` is given, a new choice of asking; else the one it had.
        `sound` is this session's own choice: whether one is played when it
        ends - never carried over from the session before.

        `instruction`: what the user wants changed from here, in their own
        words. Given, the run first AMENDS ITS CONTRACT for the line it goes
        on - from an older version, the new branch; from the latest, the
        same line, the versions before it keeping the contract they had -
        and only then goes on with the work (see `Run._amend`).

        `from_version`: continue from that version instead. One that is not
        the latest ROLLS BACK first - the work's files and the run's state
        become that version's - and the run goes on as a new branch, so
        what came after it is kept. A run rolled back may be continued
        whatever it ended as: a finished one too.

        A FINISHED RUN goes on from where it finished only with new words -
        the next thing to do, building on what is done (a new task on top of
        it): its contract is amended by them, the goal too if they call for
        it, and the work is taken up again. With nothing said, there is
        nothing to go on with.

        AN INTERRUPTED RUN goes on from its LAST MILESTONE - the head - unless
        another version is asked for: where it was in its round, and what it
        had said there, went with the application, so its files and state
        are put back as they were at that milestone, and it goes on from
        there."""
        with self._lock:
            busy = self._busy(unit)
            if busy:
                return {"error": busy}
            if unit.get("debug") and not given_room:
                waits = self._wait_for_room("continue", unit, {
                    "run_id": run_id, "limits": limits, "packages": packages,
                    "from_version": from_version, "instruction": instruction,
                    "sound": sound})
                if waits:
                    return waits
            if self.in_subprocess and not unit.get("debug"):
                return self._apart("continue", unit, run_id=run_id, limits=limits,
                                   packages=packages, from_version=from_version,
                                   instruction=instruction, sound=sound)
            rows = self.db.query("SELECT * FROM work_runs WHERE id = ?", (run_id,))
            if not rows:
                return {"error": "no such run"}
            row = self._row(rows[0])
            if row["unit_id"] != unit["id"]:
                return {"error": "that run is of other work"}
            runtime = self._runtime(unit, Path(row["folder"]))
            rolled: Dict[str, Any] = {}
            from_version = str(from_version or "").strip()
            store = VersionStore(self.run_dir_of(row), runtime.policy)
            milestone = (not from_version and row["status"] == INTERRUPTED
                         and bool(store.index()["head"]))
            if milestone:
                from_version = store.index()["head"]
            if from_version:
                index = store.index()
                if from_version not in {one["id"] for one in index["versions"]}:
                    return {"error": f"this run has no version {from_version}"}
                if from_version != index["head"] or milestone:
                    try:
                        rolled = self._roll_back(row, store, from_version,
                                                 runtime.policy)
                    except (VersionError, SecurityViolation, OSError, ValueError) as exc:
                        return {"error": f"could not roll back to {from_version}: {exc}"}
            words = str(instruction or "").strip()
            if not rolled and row["status"] == DONE and not words:
                return {"error": "the run is finished - say what to do next, "
                                 "building on it, to carry it on"}
            if not rolled and row["status"] not in (*CONTINUABLE, DONE):
                return {"error": f"a run that is {row['status']} cannot be continued"}
            run = Run(self, unit, row["instruction"], runtime,
                      limits_of(limits), resumed=row,
                      packages=mode_of(packages) if packages not in (None, "") else None,
                      amend=instruction, sound=wants_sound(sound))
            self._running[run.id] = run
            run.start()
            return {"run": run.id, "folder": row["folder"], "limits": run.limits,
                    "packages": run.packages_mode, "sound": run.sound,
                    **({"amending": True} if run.amend_words else {}),
                    **({"rolled_back": rolled} if rolled else {}),
                    **({"from_milestone": rolled.get("version", "")}
                       if milestone and rolled else {})}

    # -- each run in a process of its own (child.py) --------------------------------
    def _room_for(self, head: bool = False) -> str:
        """Why one more run may not start now - "" when it may. EVERY RUN
        COUNTS, the user's and the Experience debugger's ways alike: the
        computer (governor.py) and everything else going side by side
        (common/pool.py) must both have room. And one asked for earlier
        and still waiting goes first - `head`: asked for the first of them."""
        if self.waiting and not head:
            return "runs asked for earlier are waiting first"
        going = sum(1 for run in self._running.values() if run.alive())
        room, why = (self.governor.room(going) if self.governor is not None
                     else (True, ""))
        if room and self.pool is not None:
            room, why = self.pool.may_start("work", key="work-waiting")
        return "" if room else (why or "there is no room now")

    def _wait_for_room(self, kind: str, unit: Dict[str, Any],
                       args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """A run kept waiting while there is no room - None when there is,
        and it starts now. A debugger way waits in the same queue as the
        user's own runs, and is started where it would have been."""
        why = self._room_for()
        if not why:
            return None
        self.waiting.append({"kind": kind, "unit": unit, "args": args,
                             "at": _now(), "why": why,
                             "debug": bool(unit.get("debug"))})
        self.waited_out.pop(str(unit.get("id") or ""), None)
        return {"waiting": True, "why": why, "position": len(self.waiting)}

    def _apart(self, kind: str, unit: Dict[str, Any], **args: Any) -> Dict[str, Any]:
        """A run started in a child process - now, or, while the computer
        has no room for it, when it has (governor.py)."""
        waits = self._wait_for_room(kind, unit, args)
        if waits:
            return waits
        return self._launch(kind, unit, args)

    def waiting_of(self, unit_id: str) -> Optional[Dict[str, Any]]:
        """A run of this unit waiting for room - why, since when, and how
        far along the queue - or None."""
        for at, one in enumerate(list(self.waiting)):
            if one["unit"].get("id") == unit_id:
                return {"why": one.get("why", ""), "since": one.get("at", ""),
                        "position": at + 1, "kind": one.get("kind", "start")}
        return None

    def unwait(self, unit_id: str) -> bool:
        """A waiting run of this unit not started after all."""
        with self._lock:
            kept = [one for one in self.waiting if one["unit"].get("id") != unit_id]
            gone = len(kept) != len(self.waiting)
            self.waiting[:] = kept
        return gone

    def _launch(self, kind: str, unit: Dict[str, Any], args: Dict[str, Any]
                ) -> Dict[str, Any]:
        from .child import launch
        said = launch(self, kind, unit, **args)
        child = said.pop("child", None)
        if child is not None:
            self._running[child.id] = child
        return said

    def start_waiting(self) -> List[Dict[str, Any]]:
        """The runs asked for while there was no room, started now that there
        is - first asked, first started."""
        started = []
        while True:
            with self._lock:
                if not self.waiting:
                    return started
                head = self.waiting.pop(0)
                why = self._room_for(head=True)
                if why:
                    self.waiting.insert(0, head)
                    head["why"] = why
                    return started
                if not head.get("debug"):
                    started.append(self._launch(head["kind"], head["unit"], head["args"]))
                    continue
            # A debugger way runs in this process: started as its button
            # would, outside the lock its start takes.
            started.append(self._start_waited(head))

    def _start_waited(self, one: Dict[str, Any]) -> Dict[str, Any]:
        unit, args = one["unit"], dict(one["args"])
        try:
            if one["kind"] == "continue":
                said = self.continue_run(args.pop("run_id"), unit, given_room=True, **args)
            else:
                said = self.start(unit, given_room=True, **args)
        except Exception as exc:              # pragma: no cover - said on the way
            log.warning("a waiting run could not start", exc_info=True)
            said = {"error": str(exc)}
        if said.get("error"):
            self.waited_out[str(unit.get("id") or "")] = str(said["error"])
        return said

    # -- versions ------------------------------------------------------------------
    @staticmethod
    def run_dir_of(row: Dict[str, Any]) -> Path:
        return Path(row["folder"]) / "runs" / row["id"]

    @staticmethod
    def log_dir_of(row: Dict[str, Any]) -> Path:
        """The run's own folder of logs (see `log_dir_for`)."""
        return log_dir_for(Path(row["folder"]), row["id"], row.get("started_at") or "")

    def open_logs(self, run_id: str) -> Optional[Path]:
        """OPEN LOG FOLDER: the project's run logs shown in the machine's
        own file manager - every run's folder in it, by when it started.
        Made if it is not there yet, and only inside the application's own
        work folder: for a run whose folder is anywhere else nothing is
        made. A run whose logs are still where runs kept them before
        (`runs/<run>/.session/`) has that folder shown instead - its logs
        are there, not in `run_logs/`. None for no such run."""
        row = self.run(run_id)
        if row is None or not row.get("folder"):
            return None
        own = self.log_dir_of(row)
        if own.name == SESSION_DIR and own.is_dir():
            self.open_folder(own)
            return own
        folder = Path(row["folder"])
        logs = folder / LOGS_DIR
        if not logs.is_dir():
            work_root = (Path(self.app_policy.workspace_root) / WORK_DIR).resolve()
            # Or the folder of a way of the Experience debugger - one of the
            # subfolders made in the folder the user chose for its project,
            # and only that one: the run's own.
            from .debug_projects import variant_folder_of
            own = variant_folder_of(self.db, str(row.get("unit_id") or ""))
            if work_root not in folder.resolve().parents and not (
                    own is not None and own.resolve() == folder.resolve()):
                raise PermissionError("the run's folder is not in the application's "
                                      "work folder: no logs are made there")
            ensure_readme(logs)
        self.open_folder(logs)
        return logs

    def _roll_back(self, row: Dict[str, Any], store: VersionStore, version_id: str,
                   policy: WorkspacePolicy) -> Dict[str, Any]:
        """The run made as it was at a version: its files, the agent's files
        in granted folders (each only if the user has not changed it since),
        its plan and the work's tree. `row` is changed to match, to be
        continued from. What it spent, its packages and its log are kept:
        those happened, whichever line the work is on."""
        manifest = store.manifest(version_id)
        state = store.get_json(str(manifest.get("state") or ""))
        if not isinstance(state, dict) or not isinstance(state.get("plan"), dict):
            raise VersionError(f"version {version_id} holds no state")
        # Every row of its tree read and checked before anything is changed.
        for table, shas in (state.get("tree") or {}).items():
            for sha in shas or []:
                store.get(sha)
        current = row["plan"] or {}
        agents = dict(current.get("granted_writes") or {})
        files = restore_files(store, manifest, Path(row["folder"]), row["id"], policy)
        granted = restore_granted(store, manifest, agents, policy)
        restore_tree(store, self.db, work_scope(row["unit_id"]), state.get("tree") or {})
        for path in granted["restored"]:
            agents[path] = sha_of_file(Path(path))
        plan = dict(state["plan"])
        for name in ("sessions", "total", "used", "packages", "limits", "granted",
                     "calls", "calls_made"):
            if name in current:
                plan[name] = current[name]
        plan["granted_writes"] = agents
        branch = store.move_head(version_id)
        said = {"version": version_id, "label": manifest.get("label", ""),
                "branch": branch, "at": _now(),
                "restored": len(files["restored"]), "removed": len(files["removed"]),
                "kept": [f"kept your version of {one}" for one in granted["kept"]],
                "left": granted["left"],
                "not_restored": [*granted["refused"], *files.get("refused", [])],
                "not_kept": files["not_kept"]}
        plan["rolled_back"] = said
        row["plan"] = plan
        row["final"] = state.get("final") or {}
        row["log"] = [*row["log"], {"at": _now(), "what": "rolled back", **said}]
        return said

    def _store_of(self, run_id: str) -> Tuple[Dict[str, Any], VersionStore]:
        row = self.run(run_id)
        if row is None:
            raise VersionError("no such run")
        return row, VersionStore(self.run_dir_of(row))

    def versions(self, run_id: str) -> Dict[str, Any]:
        """A run's versions as a tree: each with its parent and its line,
        what it was saved at and in brief where the run stood - and the
        head, the one it continues from by default."""
        row, store = self._store_of(run_id)
        index = store.index()
        alive = self._alive(run_id)
        return {"run": run_id, "head": index["head"], "branch": index["branch"],
                "branches": index["branches"],
                "versions": [_slides_settled(with_message(one), alive)
                             for one in index["versions"]],
                "alive": alive, "status": row["status"]}

    def contract_in_force(self, row: Dict[str, Any], index: Dict[str, Any],
                          latest: bool = True) -> Optional[Dict[str, Any]]:
        """THE CONTRACT IN FORCE for a run: the one in its plan - the line it
        is on - with each deliverable's card as it stands now, the line, and
        the version it holds since; amended, the user's words and what
        changed from the contract before. `latest`: the run is its work's
        latest, whose tree is the one in the database - an older run's
        deliverables are shown by its contract alone."""
        from ...heartbeat.experience.store import ExperienceStore
        contract = (row.get("plan") or {}).get("contract") or {}
        if not contract.get("goal"):
            return None
        root_id = ""
        cards: Dict[str, Dict[str, Any]] = {}
        # The work's tree is its latest run's - rolled back with it - so an
        # older run's cards there may stand where another run left them.
        if latest:
            try:
                store = ExperienceStore(self.db, work_scope(row["unit_id"]))
                cards = {one["id"]: one for one in store.cards()}
                root = store.root(row["unit_id"])
                root_id = root["id"] if root else ""
            except Exception:        # pragma: no cover - shown by the contract alone
                log.debug("could not read the cards of %s", row.get("id"), exc_info=True)
        said = contract_shown(contract, cards, root_id, row.get("plan") or {})
        since = in_force_since(index) or {}
        said.update(branch=index.get("branch") or "", head=index.get("head") or "",
                    since=since.get("id", ""), since_kind=since.get("kind", ""),
                    amended=since.get("amended") or None)
        return said

    def version(self, run_id: str, version_id: str) -> Dict[str, Any]:
        """One version: its files, the agent's files in granted folders, and
        where the run stood - the contract's goal, the to-do list, the deck."""
        row, store = self._store_of(run_id)
        manifest = store.manifest(version_id)
        state = store.get_json(str(manifest.get("state") or "")) or {}
        plan = state.get("plan") or {}
        # Its contract as it was then - each deliverable as that version's
        # own tree had it - for the page to show beside, never as, the one
        # in force.
        cards: Dict[str, Dict[str, Any]] = {}
        try:
            from ...heartbeat.experience.store import ExperienceStore
            for sha in (state.get("tree") or {}).get("experience_cards") or []:
                one = store.get_json(sha)
                if isinstance(one, dict) and one.get("id"):
                    cards[one["id"]] = ExperienceStore._card(one)
        except VersionError:
            cards = {}
        wanted = {str(one.get("card") or "") for one in
                  (plan.get("contract") or {}).get("deliverables") or [] if isinstance(one, dict)}
        contract = contract_shown(plan.get("contract") or {},
                                  {k: v for k, v in cards.items() if k in wanted},
                                  plan=plan)
        return {"run": run_id, "head": store.index()["head"] == version_id,
                "contract": contract, "amended": manifest.get("amended") or None,
                "message": with_message(manifest)["message"],
                **{k: manifest[k] for k in ("round", "progress", "full_review")
                   if k in manifest},
                **{k: manifest.get(k) for k in ("id", "n", "parent", "branch", "label",
                                                "kind", "at", "summary")},
                "files": [{"path": path, "size": one.get("size", 0)}
                          for path, one in sorted((manifest.get("files") or {}).items())],
                "granted": [{"path": path, "size": one.get("size", 0)} for path, one
                            in sorted((manifest.get("granted") or {}).items())],
                "skipped": manifest.get("skipped") or [],
                "goal": (plan.get("contract") or {}).get("goal", ""),
                "phase": plan.get("phase", ""), "status": state.get("status", ""),
                "todo": plan.get("todo") or {}, "rounds": plan.get("rounds") or [],
                "deck": bool(plan.get("deck")),
                # ITS SLIDES: where the whole work stood at it, as the work
                # agent showed it then - drawn by the deck's own renderer,
                # its figures as this version holds them. A version saved
                # before versions had slides has none; one whose slides could
                # not be made says so.
                "slides": self._version_slides(store, run_id, version_id, state, manifest,
                                               alive=self._alive(run_id))}

    def _alive(self, run_id: str) -> bool:
        live = self._running.get(run_id)
        return bool(live and live.alive())

    @staticmethod
    def _version_slides(store: VersionStore, run_id: str, version_id: str,
                        state: Dict[str, Any], manifest: Dict[str, Any],
                        alive: bool = False) -> Dict[str, Any]:
        from urllib.parse import quote
        from .deck import count_of, data_uri, is_visual, render
        deck = state.get("slides") if isinstance(state, dict) else None
        if isinstance(deck, dict) and deck:
            # ITS FIGURES AS THE VERSION KEPT THEM - drawn into the version's
            # slides folder, or images the work had made - carried in the
            # page itself: whatever has happened to the files since, a past
            # version shows the slides it had. Slides kept before figures
            # were kept with them name files the version holds, served by
            # the app as before.
            figures = deck.get("figures") if isinstance(deck.get("figures"), dict) else {}

            def image(path: str) -> str:
                entry = figures.get(path)
                if isinstance(entry, dict):
                    try:
                        return data_uri(store.get(str(entry.get("sha") or "")), path)
                    except VersionError:
                        return ""
                return (f"/api/work/file?run={quote(run_id)}&path={quote(path)}"
                        f"&version={quote(version_id)}")
            page = render(deck, image)
            return {"status": "made", "count": count_of(page),
                    "title": str(deck.get("title") or ""), "page": page,
                    # Drawn, or in words - and if in words, whether because
                    # the figures could not be made.
                    "format": "drawn" if is_visual(deck) else "words",
                    **({"fallback": str(deck.get("fallback"))} if deck.get("fallback")
                       else {})}
        summary = (manifest.get("summary") or {}) if isinstance(manifest, dict) else {}
        said = summary.get("slides")
        if said == "drawing":
            return ({"status": "drawing"} if alive else
                    {"status": "not made", "why": "the run ended before they were drawn"})
        if said == "not made":
            return {"status": "not made", "why": str(summary.get("slides_why") or "")}
        return {"status": "skipped" if said == "skipped" else "none"}

    def version_deck(self, run_id: str, version_id: str) -> Optional[Dict[str, Any]]:
        """The deck as it was at a version; None if it had none."""
        _row, store = self._store_of(run_id)
        manifest = store.manifest(version_id)
        state = store.get_json(str(manifest.get("state") or "")) or {}
        return (state.get("plan") or {}).get("deck") or None

    def version_file(self, run_id: str, version_id: str, path: str) -> Optional[bytes]:
        """A file as a version holds it - by its path inside the work's
        folder, or its full path there or in a granted folder; None if the
        version does not hold it."""
        row, store = self._store_of(run_id)
        manifest = store.manifest(version_id)
        folder = Path(row["folder"])
        wanted = Path(str(path or ""))
        entry = None
        if wanted.is_absolute():
            try:
                entry = (manifest.get("files") or {}).get(
                    wanted.resolve().relative_to(folder.resolve()).as_posix())
            except (ValueError, OSError):
                # Kept under its full resolved path, as the agent wrote it.
                entry = (manifest.get("granted") or {}).get(str(wanted.resolve()))
        else:
            entry = (manifest.get("files") or {}).get(wanted.as_posix())
        return store.get(entry["sha"]) if entry else None

    def stop(self, run_id: str, now: bool = False) -> bool:
        """Stop a run: it wraps up - the deck of what exists and the to-do
        list - unless told to stop now."""
        run = self._running.get(run_id)
        if run is None:
            return False
        run.stopping = True
        if now:
            run.stop_now = True
        # A paused run is let go, to end as it would have: held, it would
        # only notice at its next look; let go, nothing holds the wrap-up.
        run.release(now)
        return True

    def pause(self, run_id: str) -> bool:
        """Pause a run that is going: it stops at its next safe point and
        does nothing until resumed. False for a run that is not going, or is
        already being stopped."""
        run = self._running.get(run_id)
        return bool(run is not None and run.pause())

    def resume(self, run_id: str) -> bool:
        """A paused run goes on exactly where it stopped. False for a run
        that is not going."""
        run = self._running.get(run_id)
        return bool(run is not None and run.resume())

    def live(self, run_id: str, since: int = 0,
             calls: int = 0) -> Optional[Dict[str, Any]]:
        """The live session of a run: its events after `since`, its model
        calls numbered after `calls` (for the chart of them), where it
        stands, its state, and what it is waiting for the user to answer.
        A run held in this application answers from memory; any other -
        one that was going when the application last closed - from what its
        folder kept. None for no such run."""
        run = self._running.get(run_id)
        if run is not None:
            return run.session(since, calls)
        row = self.run(run_id)
        if row is None:
            return None
        run_dir = self.run_dir_of(row) if row.get("folder") else None
        events = [one for one in (read_session(self.log_dir_of(row)) if run_dir else [])
                  if int(one.get("seq") or 0) > since]
        plan = row["plan"] or {}
        session = (plan.get("sessions") or [{}])[-1]
        used = session.get("used") or {}
        index = VersionStore(run_dir).index() if run_dir else {"versions": [], "head": ""}
        head = next((one for one in index["versions"] if one["id"] == index["head"]), None)
        summary = (head or {}).get("summary") or {}
        state = LIVE_INTERRUPTED if row["status"] == INTERRUPTED else LIVE_ENDED
        latest = [one["id"] for one in self.db.query(
            "SELECT id FROM work_runs WHERE unit_id = ? ORDER BY started_at DESC LIMIT 1",
            (row["unit_id"],))] == [run_id]
        in_force = self.contract_in_force(row, index, latest) if row.get("folder") else None
        return {"run": run_id, "events": events[-SESSION_BATCH:],
                "last": max([since, *[int(one["seq"]) for one in events]]),
                "calls": calls_after(plan, calls),
                "state": state, "status": row["status"], "pending": [], "requests":
                _requests_in_brief(plan),
                "progress": {"stage": plan.get("phase", ""), "phase": plan.get("phase", ""),
                             "round": int(plan.get("round") or 0),
                             "rounds_done": len(plan.get("rounds") or []),
                             "cards": summary.get("cards"),
                             "cards_done": summary.get("cards_done"),
                             "used": {"minutes": session.get("minutes"),
                                      "tokens": int(used.get("tokens_in") or 0)
                                      + int(used.get("tokens_out") or 0),
                                      "tokens_in": int(used.get("tokens_in") or 0),
                                      "tokens_out": int(used.get("tokens_out") or 0),
                                      "calls": int(used.get("calls") or 0)},
                             "limits": session.get("limits") or plan.get("limits") or {},
                             "sound": bool(session.get("sound")),
                             "activity": "", "pause_asked": False, "held": False,
                             "stopping": False},
                "contract": _contract_brief(in_force),
                # Where Continue goes on from: for a run the application
                # closed on, its last milestone.
                "resumes_from": {"version": head["id"], "label": head.get("label", "")}
                if head and state == LIVE_INTERRUPTED else None}

    def approve(self, run_id: str, request_id: str,
                allow: bool) -> Optional[Dict[str, Any]]:
        """The user's answer to a request to install packages. None when
        there is no such run going, or no such request of it; the request
        as it now stands otherwise - unchanged if it was already settled."""
        run = self._running.get(run_id)
        if run is None or not run.alive():
            return None
        return run.answer(request_id, bool(allow))

    def run_states(self) -> List[Dict[str, Any]]:
        """THE RUNS GOING NOW, AND HOW: going, paused or waiting for you -
        and, waiting, for what - so the list of projects shows at a glance
        which one needs you."""
        out = []
        for run in list(self._running.values()):
            if not run.alive():
                continue
            state = run.live_state()
            ask = run._model_ask
            waiting_on = ""
            if state == LIVE_WAITING:
                waiting_on = ("model" if ask is not None and ask.get("status") == PENDING
                              else "packages")
            out.append({"run": run.id, "unit": run.unit.get("id", ""),
                        "debug": bool(run.debug), "state": state, "waiting_on": waiting_on,
                        "why": run.waiting_for(), "activity": run.activity})
        return out

    # -- the record ---------------------------------------------------------
    def save(self, run: "Run") -> None:
        # A version's slides are drawn on a thread of their own while the
        # reviewer reviews: what one thread changes in the plan as the other
        # writes it down is read again, not a failure.
        for attempt in range(5):
            try:
                plan = json.dumps(run.plan, ensure_ascii=False, default=str)
                logged = json.dumps(run.log[-400:], ensure_ascii=False, default=str)
                final = json.dumps(run.final, ensure_ascii=False, default=str)
                break
            except RuntimeError:          # changed while it was read
                if attempt == 4:
                    raise
                time.sleep(0.01)
        row = (run.id, run.unit["id"], run.unit["name"], str(run.folder),
               run.status, run.instruction, plan, logged, final, run.started_at,
               run.ended_at, _now())
        self.db.execute(
            "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, "
            "instruction, plan, log, final, started_at, ended_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO "
            "UPDATE SET status = excluded.status, plan = excluded.plan, log = "
            "excluded.log, final = excluded.final, ended_at = "
            "excluded.ended_at, updated_at = excluded.updated_at", row)

    @staticmethod
    def _row(row: Any) -> Dict[str, Any]:
        entry = dict(row)
        for name, default in (("plan", {}), ("log", []), ("final", {})):
            try:
                entry[name] = json.loads(entry[name] or "null") or default
            except ValueError:
                entry[name] = default
        return entry

    def runs(self, unit_id: str = "") -> List[Dict[str, Any]]:
        rows = (self.db.query("SELECT * FROM work_runs WHERE unit_id = ? ORDER "
                              "BY started_at DESC LIMIT 30", (unit_id,))
                if unit_id else
                self.db.query("SELECT * FROM work_runs ORDER BY started_at "
                              "DESC LIMIT 30"))
        out = []
        for row in rows:
            entry = self._row(row)
            live = self._running.get(entry["id"])
            entry["alive"] = bool(live and live.alive())
            entry["stopping"] = bool(live and live.alive() and live.stopping)
            if entry["alive"]:
                # As it is this moment - paused the moment it was asked, not
                # when the run next saves itself.
                entry["status"] = live.status
                entry["paused"] = live.pause_asked
            entry["continuable"] = (not entry["alive"]
                                    and entry["status"] in CONTINUABLE)
            # Finished: carried on only with words - the next thing to do.
            entry["extendable"] = not entry["alive"] and entry["status"] == DONE
            # Its versions in brief - how many, and the one it goes on from;
            # the tree itself is asked for when it is opened.
            index = (VersionStore(self.run_dir_of(entry)).index() if entry.get("folder")
                     else {"versions": [], "head": "", "branch": ""})
            head = next((one for one in index["versions"] if one["id"] == index["head"]),
                        {})
            entry["versions"] = {"count": len(index["versions"]), "head": index["head"],
                                 "branch": index["branch"],
                                 # Whose slides are still being drawn - the
                                 # tree is asked for again as each is added.
                                 "drawing": sum(1 for one in index["versions"]
                                                if (one.get("summary") or {}).get("slides")
                                                == "drawing") if entry["alive"] else 0}
            # Its last milestone - where Continue goes on from for a run the
            # application closed on.
            entry["milestone"] = ({"version": head["id"], "label": head.get("label", "")}
                                  if head else None)
            # The contract in force, with where each deliverable stands - the
            # work's tree is its latest run's, the first listed of its work.
            latest = not any(one["unit_id"] == entry["unit_id"] for one in out)
            entry["contract_in_force"] = self.contract_in_force(entry, index, latest)
            # THE FULL TRANSCRIPT, as the files it is kept in - and where
            # they are: the project's run logs, and this run's folder in
            # them (where an older run's are, if it kept them in its own).
            own_logs = self.log_dir_of(entry) if entry.get("folder") else None
            entry["transcript"] = ([str(one) for one in transcript_files(own_logs)]
                                   if own_logs else [])
            entry["log_dir"] = (str(Path(entry["folder"]) / LOGS_DIR)
                                if entry.get("folder") else "")
            entry["run_log_dir"] = str(own_logs) if own_logs else ""
            # ITS MODEL CALLS, one by one, for the chart of them over time -
            # as the run holds them this moment while it goes - given once,
            # here, and not again inside its plan.
            plan = entry.get("plan") or {}
            entry["calls"] = calls_after(live.plan if entry["alive"] else plan)
            plan.pop("calls", None)
            # Whether a sound is played when its session ends - the latest
            # session's own choice.
            entry["sound"] = bool(((plan.get("sessions") or [{}])[-1] or {}).get("sound"))
            out.append(entry)
        return out

    def run(self, run_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM work_runs WHERE id = ?", (run_id,))
        return self._row(rows[0]) if rows else None

    def tree(self, unit: Dict[str, Any]) -> Dict[str, Any]:
        """The work's own tree, as the page draws it: its cards in order,
        every stage with its summaries and reviews, and the edges."""
        from ...heartbeat.experience.stages import StageWriter, level_of
        from ...heartbeat.experience.store import ExperienceStore
        scope = work_scope(unit["id"])
        store = ExperienceStore(self.db, scope)
        if store.root(unit["id"]) is None:
            return {"unit": unit, "cards": [], "stages": [], "edges": []}
        writer = StageWriter(self.db, None, None, scope=scope)
        work_unit = _work_unit(unit, "")
        root, cards = writer.tree(work_unit)
        children: Dict[str, List[Dict[str, Any]]] = {}
        for card in cards.values():
            if card["id"] != root["id"]:
                children.setdefault(card["parent_id"], []).append(card)
        ordered: List[Dict[str, Any]] = []

        def walk(card: Dict[str, Any], depth: int) -> None:
            ordered.append(dict(card, depth=depth, level=level_of(depth)))
            for child in children.get(card["id"], []):
                walk(child, depth + 1)

        walk(root, 0)
        stages = store.stages([unit["id"]])
        from ...heartbeat.experience.index import StageIndex, found_by
        found = found_by(StageIndex(self.db, scope), [s["id"] for s in stages])
        for stage in stages:
            stage["found_by"] = found.get(stage["id"], {"words": [], "places": []})
        return {"unit": unit, "cards": ordered, "stages": stages,
                "edges": store.edges([s["id"] for s in stages])}


def _contract_brief(in_force: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The contract in force in a line, for the live session's head: its
    goal, the line, and its deliverables done of all."""
    if not in_force:
        return None
    return {"goal": str(in_force.get("goal") or ""), "branch": in_force.get("branch", ""),
            "since": in_force.get("since", ""), "done": in_force.get("done", 0),
            "total": in_force.get("total", 0),
            "amended": bool(in_force.get("instructions"))}


def _settle_requests(plan: Dict[str, Any], why: str) -> bool:
    """Every request still waiting for an answer, ended as not approved.
    Whether there was one."""
    settled = False
    for request in (plan.get("packages") or {}).get("requests") or []:
        if request.get("status") == PENDING:
            request.update(status=UNANSWERED, answered_at=_now(), note=why)
            settled = True
    # A wait on the model, too: a session continued looks at the model anew.
    for ask in plan_waits(plan):
        if ask.get("status") == PENDING:
            ask.update(status=UNANSWERED, answered_at=_now(), note=why)
            settled = True
    return settled


def _requests_in_brief(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Every request the run made of the user, by its id, as it now stands -
    so the session shows each answered one with its answer."""
    out = {}
    for one in list((plan.get("packages") or {}).get("requests") or []):
        if isinstance(one, dict) and one.get("id"):
            out[one["id"]] = {"status": one.get("status", ""), "note": one.get("note", ""),
                              "kind": "packages"}
    for one in plan_waits(plan):
        out[one["id"]] = {"status": one.get("status", ""), "note": one.get("note", ""),
                          "kind": "model"}
    return out


def _slides_settled(entry: Dict[str, Any], alive: bool) -> Dict[str, Any]:
    """A version as the tree shows it: slides still "drawing" for a run that
    is no longer going were never drawn - the session ended first."""
    summary = entry.get("summary") or {}
    if alive or summary.get("slides") != "drawing":
        return entry
    return dict(entry, summary=dict(summary, slides="not made",
                                    slides_why="the run ended before they were drawn"))


def plan_waits(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The waits on the model a run's plan kept."""
    return [one for one in list(plan.get("model_waits") or [])
            if isinstance(one, dict) and one.get("id")]


#: Where a provider's credit is bought: only paying mends a quota of credit.
BILLING_PAGES = {"anthropic": "https://console.anthropic.com/settings/billing"}


def _model_as_permission(ask: Dict[str, Any]) -> Dict[str, Any]:
    """A wait on the model, as the session shows a request of the user's:
    what failed and why, what to do and where, when it looks again by
    itself - answered with POST /api/work/approve: allow is "try again
    now", deny "end the run"."""
    trouble = ask.get("trouble", "")
    provider, name = ask.get("provider", ""), ask.get("provider_name", "") or "the model"
    state = ask.get("state", "")
    if trouble == MODEL_STUCK:
        title = f"{name} has not answered for {_seconds_said(TEMPORARY_CAP_SECONDS)}"
        fix = {"action": "settings", "provider": provider,
               "label": "Choose another model in Settings"}
        note = ("It stopped trying by itself, so as not to spend your usage on calls that "
                "do not answer: press Try again when you want it to go on.")
    elif trouble == MODEL_LIMIT:
        title = f"{name}: usage limit reached"
        fix = {"action": "settings", "provider": provider,
               "label": "Choose another model in Settings"}
        note = ("It goes on by itself a minute after the limit resets." if ask.get("resets_at")
                else "The provider did not say when it resets: it looks again every "
                     f"{int(LIMIT_LOOK_SECONDS // 60)} minutes and goes on once it answers.")
    else:
        sign_in = state in ("authentication_required", "credential_invalid")
        title = f"{name} needs you" + (": sign in again" if sign_in else "")
        fix = {"action": "sign_in" if sign_in else "settings", "provider": provider,
               "label": f"Sign in to {name} again" if sign_in
               else "Open Settings - Models"}
        if any(one in str(ask.get("reason", "")).lower() for one in _PAID_WORDS) \
                and provider in BILLING_PAGES:
            fix["url"] = BILLING_PAGES[provider]
            fix["url_label"] = f"{name} billing"
        note = ("Mend it, then press Try again - or leave it: it looks again now and then, "
                "and goes on by itself once the model answers.")
    return {"id": ask.get("id", ""), "kind": "model", "trouble": trouble, "title": title,
            "what": ask.get("reason", ""), "provider": provider, "provider_name": name,
            "state": state, "asked_at": ask.get("asked_at", ""),
            "status": ask.get("status", ""), "resets_at": ask.get("resets_at", ""),
            "next_look": ask.get("next_look", ""), "looks": int(ask.get("looks") or 0),
            "fix": fix, "note": note + " Waiting does not count against its minutes."}


def _as_permission(request: Dict[str, Any]) -> Dict[str, Any]:
    """A request waiting for the user, as the session shows any kind of
    permission: what is asked, why, and how to answer it (POST
    /api/work/approve with its id)."""
    packages = [str(one) for one in request.get("packages") or []]
    return {"id": request.get("id", ""), "kind": "packages",
            "title": "Install Python packages", "what": ", ".join(packages),
            "items": packages, "why": request.get("why", ""),
            "asked_at": request.get("asked_at", ""), "status": request.get("status", ""),
            "note": ("From PyPI, wheels only, into this run's own folder. The run "
                     "waits for your answer; the wait does not count against its "
                     "minutes.")}


def _work_unit(unit: Dict[str, Any], instruction: str) -> Dict[str, Any]:
    """The work's own tree is kept under the unit's id, as a unit of one."""
    return {"id": unit["id"], "kind": unit.get("kind", "project"),
            "name": unit.get("name", ""),
            "why": instruction or unit.get("why", ""), "status": "",
            "tasks": [SimpleNamespace(id=unit["id"], members=[], described={})]}


#: Versions that get no slides: a contract's - there is no work to show yet.
NO_SLIDES_FOR = ("contract", "amended")


class _SlidesBehind:
    """THE VERSIONS' SLIDES, DRAWN BEHIND THE WORK: each saved version's,
    one at a time and in order - each deck builds on the one before - on a
    thread of their own, while the run goes on with its next step; each is
    added to its version when it is drawn. The run waits for them only when
    its session ends."""

    def __init__(self, run: "Run") -> None:
        self.run = run
        self._jobs: deque = deque()
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._thread: Optional[threading.Thread] = None
        #: The version being drawn now, and those waiting their turn.
        self.drawing = ""

    def add(self, job: Dict[str, Any]) -> None:
        with self._lock:
            self._jobs.append(job)
            self._idle.clear()
            if self._thread is None:
                self._thread = threading.Thread(target=self._go, name=f"{self.run.id}-slides",
                                                daemon=True)
                self._thread.start()

    def waiting(self) -> List[str]:
        with self._lock:
            return ([self.drawing] if self.drawing else []) + [
                one["entry"]["id"] for one in self._jobs]

    def _go(self) -> None:
        while True:
            with self._lock:
                if not self._jobs:
                    self.drawing = ""
                    self._thread = None
                    self._idle.set()
                    return
                job = self._jobs.popleft()
                self.drawing = job["entry"]["id"]
            try:
                self.run._draw_behind(job)
            except BaseException:      # the thread's own: said, not raised
                log.warning("the slides of %s could not be drawn behind the work",
                            self.run.id, exc_info=True)

    def wait(self) -> None:
        self._idle.wait()


class Run:
    """One run: a contract, rounds of work each checked and reviewed, a
    final review, and the deck of its results - paused, not rushed, when a
    limit comes, and continued from there."""

    #: Seconds spent waiting for the user's answers (set per run, below).
    waited = 0.0
    #: Seconds held paused at a safe point, and when the hold going on now
    #: began (0: none) - neither is the run's time (set per run, below).
    paused_for = 0.0
    _held_since = 0.0

    def __init__(self, owner: WorkRuns, unit: Dict[str, Any], instruction: str,
                 runtime: SandboxRuntime, limits: Dict[str, int],
                 resumed: Optional[Dict[str, Any]] = None,
                 packages: Optional[str] = None, amend: str = "",
                 sound: bool = False) -> None:
        from ...heartbeat.experience.retrieve import Retriever
        from ...heartbeat.experience.stages import StageWriter
        from ...heartbeat.experience.store import ExperienceStore, LIVE
        self.owner = owner
        self.unit = unit
        #: A WAY OF THE EXPERIENCE DEBUGGER (see `debug_projects.py`), not a
        #: piece of the user's own work: nothing of it is looked up in, or
        #: written to, the user's learned experience - the full way only
        #: READS it, as the reviewer of their own work does. Any other run is
        #: FULL, exactly as it always was.
        debug = unit.get("debug") if isinstance(unit.get("debug"), dict) else {}
        self.debug = bool(debug)
        self.variant = str(debug.get("variant") or FULL) if debug else FULL
        #: The single model's tool calls so far - a turn with none, that does
        #: not say it is done, has not moved the work.
        self._worker_tools = 0
        self.instruction = instruction.strip()
        #: The user's words this session was continued with - the contract
        #: is amended by them before any more work. Only a continued run has
        #: a contract to amend.
        self.amend_words = str(amend or "").strip()[:INSTRUCTION_CHARS] if resumed else ""
        #: An amendment approved, waiting for the version that carries it.
        self._amended: Optional[Dict[str, Any]] = None
        self.runtime = runtime
        self.limits = limits
        #: A SOUND WHEN THIS SESSION ENDS - the user's choice for it alone:
        #: played once, as it ends, however it ends (see `_ring`).
        self.sound = bool(sound)
        self._rung = False
        self.folder = Path(runtime.policy.write_root)
        self.resumed = resumed is not None
        self.id = resumed["id"] if resumed else f"run_{uuid.uuid4().hex[:16]}"
        self.status = "running"
        self.log: List[Dict[str, Any]] = list(resumed["log"]) if resumed else []
        self.final: Dict[str, Any] = {}
        self.started_at = resumed["started_at"] if resumed else _now()
        #: ITS LOGS - the live session and the full transcript - in the
        #: project's run logs, a folder of its own named by when it started
        #: (or, for a run from before, where it kept them: see `log_dir_for`).
        self.log_dir = log_dir_for(self.folder, self.id, self.started_at)
        self.ended_at = ""
        self.stopping = False
        self.stop_now = False
        self.stop_reason = ""
        self._thread: Optional[threading.Thread] = None
        self.began = time.time()
        #: Seconds spent waiting for the user to answer a request: not the
        #: run's time, so not counted against its limit.
        self.waited = 0.0
        #: The requests being waited on, each with what wakes its wait.
        self._answers: Dict[str, threading.Event] = {}
        self._answer_lock = threading.Lock()
        #: WAITING ON THE MODEL (`_wait_for_model`): the wait going on - one
        #: for every thread whose call failed - and, once a wait ended the
        #: run, why: then no other call is made. `_model_kick` moves when you
        #: say to try again now.
        self._model_ask: Optional[Dict[str, Any]] = None
        self._model_lock = threading.RLock()
        self._model_shut = ""
        self._model_kick = 0
        #: Threads waiting on the model now, and since when: the time is
        #: counted once, however many wait together (`_waiting_ends`).
        self._model_waiters = 0
        self._model_waiting_since = 0.0
        #: THE VERSIONS' SLIDES, drawn behind the work (`_SlidesBehind`).
        self._behind = _SlidesBehind(self)
        #: PAUSING. Clear while the user has the run paused: the run looks at
        #: it at each safe point - before a model call, before a tool call,
        #: between rounds - and is held there, doing nothing, until it is set
        #: again; then it goes on exactly there, its conversation in memory.
        self._unpaused = threading.Event()
        self._unpaused.set()
        self.paused_for = 0.0
        self._held_since = 0.0
        #: THE LIVE SESSION: its last events in memory, numbered in order
        #: across the run's sessions, and kept in its folder as they come.
        self._events: deque = deque(maxlen=SESSION_EVENTS)
        self._events_lock = threading.Lock()
        self._seq = 0
        #: What it is doing this moment, and which part of the run it is in.
        self.activity = ""
        self.stage_now = ""
        self._cards_seen: Tuple[float, Dict[str, int]] = (0.0, {})
        #: The contract in force in a line, as last counted (see `_count_cards`).
        self._contract_seen: Optional[Dict[str, Any]] = None
        if resumed:
            for one in read_session(self.log_dir):
                self._events.append(one)
                self._seq = max(self._seq, int(one["seq"]))
        #: This session's use; earlier sessions' is in the plan.
        self.used = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        #: What the call going now has used so far - a call that runs its
        #: tools itself (Claude Code, natively) is many rounds of the model -
        #: counted toward the limits until the call's own count is added.
        self._inflight: Dict[int, Dict[str, int]] = {}
        #: The bookkeeping of a call that ended - its tokens, its record, its
        #: transcript - one at a time: two calls can end together.
        self._books = threading.RLock()
        #: What its cards, summaries and slides are written in: the user's
        #: display language, as set when the run started.
        self.language = owner.experience.language() \
            if hasattr(owner.experience, "language") else "English"
        self.scope = work_scope(unit["id"])
        self.store = ExperienceStore(owner.db, self.scope)
        self.writer = StageWriter(owner.db, None, None, scope=self.scope)
        self.work_unit = _work_unit(unit, self.instruction)
        experience = owner.experience
        # The debugger's units are not the user's: no record of theirs is
        # looked up for one, and none is ever written through one (the
        # project's record, and its card-opening, are what could make one).
        own_work = experience is not None and not self.debug
        #: THE PROJECT AS THE RECORD HAS IT (`live`), and when it was read.
        self._live_unit = experience.full_unit(unit["id"]) if own_work else None
        self._live_at = time.time()
        self.live_writer = experience.stages_writer() if own_work else None
        # The user's experience, READ: by the reviewer of the full way only.
        self.retriever = Retriever(owner.db, getattr(experience, "library", None),
                                   scope=LIVE) if experience and self.variant == FULL else None
        self.plan: Dict[str, Any] = dict(resumed["plan"]) if resumed else {
            "phase": "contract", "round": 0, "stalls": 0, "fix_rounds": 0,
            "rounds": [], "decisions": [], "blocked": [], "amendments": [],
            "changed_files": [], "sessions": [],
            "total": {"calls": 0, "tokens_in": 0, "tokens_out": 0}}
        self.plan["limits"] = limits
        self.plan["granted"] = [str(one) for one in runtime.policy.extra_writable]
        # Packages: how the user wants to be asked - kept from the run's
        # last session unless they chose again - and every request made.
        book = self.plan.setdefault("packages", {})
        book["mode"] = packages if packages in (ASK, AUTO) else mode_of(book.get("mode"))
        book.setdefault("requests", [])
        book.setdefault("installed", [])
        _settle_requests(self.plan, "the run ended while it waited")
        self.installer = owner.installer_for(runtime.policy)
        self.plan["used"] = self.used
        self.plan.setdefault("sessions", []).append(
            {"started": _now(), "limits": limits, "used": self.used,
             "sound": self.sound})
        #: ITS MODEL CALLS, one by one - when each began and ended, who made
        #: it, in which part of the run, what went in and what came out -
        #: for the chart of them over time. Across its sessions, the last
        #: `CALLS_KEPT`; each numbered in order, the count kept beside them.
        self.plan.setdefault("calls", [])
        self.plan.setdefault("calls_made", len(self.plan["calls"]))
        if self.resumed:
            self._reopen()
        # The single model has the work agent's tools to read, run, search the
        # web and write - and no cards or stages, which it has none of.
        alone = self.variant == SINGLE
        self.tools = WorkTools(runtime, log=self._tool_used,
                               cards=None if alone else self._open_cards,
                               find=None if alone else self._find_in_project,
                               changed=self._changed, install=self._install,
                               stages=None if alone or self.retriever is None
                               else self._open_stages)
        # A wait for one of its jobs ends when the run is stopped.
        self.tools.stopped = lambda: self.stop_now or self.stopping
        # The user's own records, to search while it plans in detail and works.
        self.tools.records_db = None if alone else getattr(owner, "db", None)
        #: Its versions, kept in its own folder - written through the work's
        #: policy, like everything else it writes.
        self.versions = VersionStore(self.run_dir, runtime.policy)
        #: THE FULL TRANSCRIPT: every model call of both agents, whole, as
        #: plain text beside the live session - which it is locked with.
        self.transcript = Transcript(runtime, self.folder, self.log_relative)
        if self.log_dir.parent.name == LOGS_DIR:
            # The project's run logs say what is in them - written once.
            try:
                ensure_readme(self.folder / LOGS_DIR, write=lambda name, text:
                              runtime.write_text(f"{LOGS_DIR}/{name}", text))
            except Exception:        # pragma: no cover - a nicety
                log.debug("could not write the run logs' README", exc_info=True)
        # The run logs are SEALED on the run's own thread, before any code of
        # it starts (`_seal_logs`) - not here, on the request that started
        # it: sealing waits for Windows' grant lock, which the first
        # debugger run holds for seconds while it seals the application's
        # code, and a second way's Start then sat greyed out with nothing
        # said, as if it could not be pressed.
        #: The part of the run a model call is recorded under, when it is not
        #: the part the run is in (the slides of a version) - per thread: the
        #: slides are drawn while the reviewer reviews (`_part`).
        self._part = ""
        # The versions are no tool's to write or delete, as the contract is
        # not - nor the logs, the record of what happened: the project's
        # run logs, every run's, and where a run from before kept its own.
        self.tools.locked = [self.contract_path, self.versions.root,
                             self.folder / LOGS_DIR, self.run_dir / SESSION_DIR,
                             self.slides_root]
        self.tools.packages_dir = self.packages_dir
        # The user's own instruction: the addresses in it exist.
        self.tools.know_addresses(self.instruction)
        # A reviewer with no context searches no past stages: the tool is not
        # offered it at all.
        self.review_tools = WorkTools(self._checking_runtime(),
                                      log=lambda name, said: self._tool_used(
                                          name, said, who="reviewer"),
                                      cards=self._open_cards,
                                      find=None if self.variant == NO_CONTEXT
                                      else self._find_anywhere,
                                      stages=None if self.variant == NO_CONTEXT
                                      or self.retriever is None else self._open_stages)
        self.review_tools.locked = list(self.tools.locked)
        self.review_tools.runs_code = ("run_python",)
        self.review_tools.know_addresses(self.instruction)
        # The user's words for this line - said now, or when it was amended
        # before - are theirs too: the addresses in them exist.
        for words in [self.amend_words, *self._branch_words()]:
            self.tools.know_addresses(words)
            self.review_tools.know_addresses(words)

    @property
    def run_dir(self) -> Path:
        return self.folder / "runs" / self.id

    @property
    def contract_path(self) -> Path:
        return self.run_dir / "contract.json"

    @property
    def slides_root(self) -> Path:
        """Where its versions' slides are drawn: a folder per version - out
        of every version's files (versions.SKIP_IN_RUN), kept with each
        version's slides instead."""
        return self.run_dir / SLIDES_DIR

    @property
    def packages_dir(self) -> Path:
        return self.run_dir / PACKAGES_DIR

    @property
    def session_dir(self) -> Path:
        """Where its live session and transcript are kept: its logs."""
        return self.log_dir

    @property
    def log_relative(self) -> str:
        """Its logs' folder inside the work's folder - as the runtime
        writes."""
        return self.log_dir.relative_to(self.folder).as_posix()

    @property
    def packages_mode(self) -> str:
        return self.plan["packages"]["mode"]

    # -- packages ------------------------------------------------------------------
    def _install(self, packages: List[str], why: str) -> Dict[str, Any]:
        """install_packages: checked, then - asked first if the user wants
        that - installed by pip under the guard, into this run's folder."""
        allowed, refused = validate(packages)
        if refused:
            self._note("packages refused", packages=list(packages)[:20], why=refused)
            return {"error": "refused: " + "; ".join(refused), "installed": []}
        why = str(why or "").strip()
        if not why:
            return {"error": "say why the packages are needed (the why argument)",
                    "installed": []}
        book = self.plan["packages"]
        request = {"id": f"req_{uuid.uuid4().hex[:10]}", "packages": allowed,
                   "why": why[:600], "asked_at": _now(), "mode": book["mode"],
                   "status": PENDING if book["mode"] == ASK else APPROVED}
        book["requests"].append(request)
        if book["mode"] == ASK:
            self._note("asked to install packages", packages=allowed, why=why[:300],
                       request=request["id"])
            self._wait_for(request)
            # Approved while paused: nothing is installed until resumed.
            self._hold("before installing the packages")
            if request["status"] == APPROVED and (self.stop_now or self.stopping):
                request.update(status=UNANSWERED, note="the run was stopped before "
                                                       "they were installed")
            if request["status"] != APPROVED:
                note = f" ({request['note']})" if request.get("note") else ""
                return {"approved": False, "installed": [],
                        "message": f"The user did not approve installing "
                                   f"{', '.join(allowed)}{note}. Do not ask for "
                                   "them again in this round: work without them, "
                                   "or say what is missing."}
        said = self.installer.install(self.run_dir, allowed)
        request.update(status=FAILED if said.get("error") else INSTALLED,
                       installed=said.get("installed") or [],
                       error=said.get("error", ""), done_at=_now())
        for one in said.get("installed") or []:
            book["installed"] = [kept for kept in book["installed"]
                                 if kept.get("name", "").lower() != one["name"].lower()]
            book["installed"].append(dict(one, at=_now(), request=request["id"],
                                          asked=book["mode"] == ASK))
        self._note("installed packages" if not said.get("error") else "install failed",
                   packages=allowed, mode=book["mode"],
                   installed=[f"{one['name']} {one['version']}"
                              for one in said.get("installed") or []],
                   error=said.get("error", ""))
        self._changed("installed packages", [], {})
        return said

    def _wait_for(self, request: Dict[str, Any]) -> None:
        """Wait for the user's answer. The run says it is waiting for them;
        stopping it - either way - ends the wait unanswered."""
        event = threading.Event()
        with self._answer_lock:
            self._answers[request["id"]] = event
        self.status = WAITING
        self.activity = "waiting for you: " + _clip(
            "install " + ", ".join(request.get("packages") or []), 100)
        self.owner.save(self)
        began = time.time()
        try:
            while not event.wait(WAIT_STEP):
                if self.stop_now or self.stopping:
                    with self._answer_lock:
                        if request["status"] == PENDING:
                            request.update(status=UNANSWERED, answered_at=_now(),
                                           note="the run was stopped while it waited")
                    break
        finally:
            with self._answer_lock:
                self._answers.pop(request["id"], None)
            self.waited += time.time() - began
            # Paused while it waited: still paused once it is answered.
            self.status = PAUSED if self.pause_asked else "running"
            self.activity = ""
            self._note("package request " + request["status"], request=request["id"])

    def answer(self, request_id: str, allow: bool) -> Optional[Dict[str, Any]]:
        """The user's answer to a request; None for no such request. For a
        wait on the model, yes is "try again now", no "end the run"."""
        with self._model_lock:
            ask = next((one for one in self.plan.get("model_waits") or []
                        if one.get("id") == request_id), None)
            if ask is not None:
                if ask.get("status") == PENDING:
                    if allow:
                        self._model_kick += 1
                        self._event("you", "answer", "You said to try the model again now",
                                    request=request_id)
                    else:
                        self._settle_model(ask, ENDED, "you ended the run")
                return dict(ask)
        with self._answer_lock:
            request = next((one for one in self.plan["packages"]["requests"]
                            if one.get("id") == request_id), None)
            if request is None:
                return None
            if request["status"] == PENDING and request_id in self._answers:
                request.update(status=APPROVED if allow else DENIED,
                               answered_at=_now())
                self._answers[request_id].set()
            return dict(request)

    def _reopen(self) -> None:
        """Continued: what made it stop is put aside - its stalls, the
        cards it set aside as blocked - and the to-do list is where the
        first round starts."""
        self.plan["stalls"] = 0
        self.plan["resumed_from"] = self.plan.get("todo") or {}
        for blocked in self.plan.get("blocked") or []:
            card = self.store.card(blocked.get("card", ""))
            if card is not None and card["status"] == "set_aside":
                self.store.update_card(card["id"], {
                    "status": "open", "status_why": "reopened when the run was continued"})
        self.plan["blocked"] = []
        if self.plan.get("phase") in ("presenting", "done"):
            self.plan["phase"] = "rounds"

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        self.owner.save(self)
        # Held before the thread starts, given back as it ends - whichever
        # way it ends (see WorkRuns.hold_code).
        held = self.owner.hold_code(self) if hasattr(self.owner, "hold_code") else None
        self._thread = threading.Thread(target=self._run_holding, args=(held,),
                                        name=self.id, daemon=True)
        try:
            self._thread.start()
        except BaseException:
            if held is not None:
                self.owner.release_code(held)
            raise

    def _seal_logs(self) -> None:
        """THE RUN LOGS SEALED, before any code of this run starts - and
        those of a project from before, on its next run: on Windows what the
        run's folder grant passes down stops at them, so no container reads
        or writes them; the user, and this application writing them as the
        user, keep their access. Elsewhere they are hidden from each child as
        it starts (runtime/isolation.py), which seals them again too."""
        if not self.owner.sandbox_enabled:
            return
        try:
            from ...runtime.isolation import seal_from_containers
            seal_from_containers(self.runtime.policy.closed_paths)
        except Exception:        # pragma: no cover - sealed again per child
            log.warning("could not seal the run logs of %s", self.id, exc_info=True)

    def _run_holding(self, held: Optional[Path]) -> None:
        try:
            self._seal_logs()
            self._run()
        finally:
            if held is not None:
                self.owner.release_code(held)

    # -- the record ------------------------------------------------------------------
    def _note(self, what: str, **said: Any) -> None:
        entry = {"at": _now(), "what": what, **said}
        self.log.append(entry)
        # The same moment, as a line of the live session.
        shown = _note_as_event(entry)
        if shown is not None:
            whole = _note_as_event(entry, whole=True)
            self._event(*shown[:3], **dict(shown[3], full=whole[2] if whole else shown[2]))
        self._count_cards()
        self.owner.save(self)

    def _tool_used(self, name: str, said: Dict[str, Any], who: str = "worker") -> None:
        if who == "worker":
            self._worker_tools += 1
        result = json.dumps(said.get("result"), ensure_ascii=False, default=str)
        self.log.append({"at": _now(), "what": "tool", "tool": name,
                         "arguments": said.get("arguments"),
                         "result": result[:1200]})
        if "result" not in said:
            # Refused before it ran (a search or a page): said as it is.
            self._event(who, "tool", f"{name}: {_clip(said.get('why') or '', 110)}",
                        tool=name, ok=False)
            return
        args = _short_args(name, said.get("arguments"))
        outcome, ok = _short_result(name, said.get("result"))
        self._event(who, "tool", f"{name}({args}) → {outcome}", tool=name,
                    args=args, result=outcome, ok=ok,
                    full=_tool_in_full(name, said.get("arguments"), said.get("result")))

    # -- the live session ----------------------------------------------------------------
    def _event(self, who: str, kind: str, text: str, **detail: Any) -> Dict[str, Any]:
        """A line of the live session: who (the worker, the reviewer, the
        run, or you), what kind of moment, and what happened, in short. Held
        in memory and kept in the run's folder - through its policy, like
        everything else it writes. Never a reason for the work to fail."""
        # THE WHOLE OF IT, ALWAYS: the line is a preview; whatever it leaves
        # out - a long argument, code, an answer, every comment - is kept
        # with it, whole, for the page to show. Nothing said is unreachable.
        full = detail.pop("full", None)
        shown = _clip(text, 400)
        full = str(full if full is not None else text)[:EVENT_FULL_CHARS]
        if " ".join(full.split()) != shown:
            detail["full"] = full
        with self._events_lock:
            self._seq += 1
            entry: Dict[str, Any] = {"seq": self._seq, "at": _now(), "who": who,
                                     "kind": kind, "text": shown}
            if detail:
                entry["detail"] = json.loads(json.dumps(detail, ensure_ascii=False,
                                                        default=str))
            self._events.append(entry)
            try:
                from ...common.agent_writes import mark_written
                relative = f"{self.log_relative}/{SESSION_FILE}"
                # The application's own record: not heard as the user's
                # activity by the listener.
                mark_written(str(self.folder / relative))
                self.runtime.write_text(relative, json.dumps(entry, ensure_ascii=False)
                                        + "\n", append=True)
            except Exception:        # pragma: no cover - the memory still has it
                log.debug("could not keep a session event of %s", self.id, exc_info=True)
        return entry

    def _stage(self, stage: str, text: str) -> None:
        """A part of the run begins: the contract, a round, the final review,
        the deck."""
        self.stage_now = stage
        self._event("run", "milestone", text, stage=stage)

    @property
    def pause_asked(self) -> bool:
        return not self._unpaused.is_set()

    def _paused_seconds(self) -> float:
        held = time.time() - self._held_since if self._held_since else 0.0
        return self.paused_for + held

    def _minutes(self) -> float:
        """The run's own time this session: not the time it waited for the
        user's answers, nor the time it was held paused."""
        return max(0.0, (time.time() - self.began - self.waited
                         - self._paused_seconds()) / 60)

    def pause(self, why: str = "") -> bool:
        """Asked to pause: from its next safe point it does nothing until
        resumed. Not a run being stopped - that is ending anyway. `why`: the
        agent paused it, for this reason (the computer short of memory)."""
        if not self.alive() or self.stopping or self.stop_now:
            return False
        if self.pause_asked:
            return True
        self._unpaused.clear()
        # Waiting for an answer, it still is; wrapping up, its status is
        # already how it ended, and stays so.
        if self.status == "running":
            self.status = PAUSED
        if why:
            self._event("run", "pause", f"Paused because {why}: it goes on by itself "
                        "when that passes, or when you resume it")
        else:
            self._event("you", "pause", "You paused the run: it stops at the next safe "
                        "point, and nothing runs until you resume it")
        self._set_status()
        return True

    def resume(self, why: str = "") -> bool:
        if not self.alive():
            return False
        if not self.pause_asked:
            return True
        if self.status == PAUSED:
            self.status = WAITING if self._answers else "running"
        self._unpaused.set()
        self._event(*(("run", "resume", f"Resumed: {why}") if why else
                      ("you", "resume", "You resumed the run: it goes on where it stopped")))
        self._set_status()
        return True

    def release(self, now: bool = False) -> None:
        """Stopped while paused: let go, to wrap up - or end, stopped now -
        as it would have."""
        if self.pause_asked:
            if self.status == PAUSED:
                self.status = "running"
            self._unpaused.set()
        if not self.alive():
            return
        self._event("you", "stop", "You stopped the run now: it ends at once" if now
                    else "You stopped the run: it wraps up - the deck of what "
                         "exists, and a to-do list")

    def _set_status(self) -> None:
        """The status as it is now, written alone - from the page's thread,
        so not the whole record, which the run's own thread is changing."""
        try:
            self.owner.db.execute("UPDATE work_runs SET status = ?, updated_at = ? "
                                  "WHERE id = ?", (self.status, _now(), self.id))
        except Exception:        # pragma: no cover - shown from memory anyway
            log.debug("could not write the status of %s", self.id, exc_info=True)

    def _hold(self, where: str) -> None:
        """A SAFE POINT. Paused: held here - no model call, no tool, no
        work - until resumed or stopped, and the time held is not the run's.
        Not paused: on at once."""
        if self._unpaused.is_set():
            return
        before = self.activity
        self.activity = f"paused {where}"
        self._event("run", "pause", f"Paused {where}: nothing runs until you resume",
                    where=where)
        self.owner.save(self)
        # Held from here - once its record says so: whatever looks at it as
        # held finds nothing of it left to write.
        self._held_since = time.time()
        try:
            while not self._unpaused.wait(WAIT_STEP):
                if self.stop_now or self.stopping:
                    break
        finally:
            self.paused_for += time.time() - self._held_since
            self._held_since = 0.0
            self.activity = before
        if not (self.stop_now or self.stopping):
            self._event("run", "resume", f"Going on {where.replace('before ', 'with ', 1)}",
                        where=where)

    def _count_cards(self) -> None:
        """How many cards the work has, and how many are done - counted by
        the run's own thread each time it records a step (a few times a
        round, not per tool call), so the page asking often reads only what
        was counted, and nothing is read from the tree on its thread."""
        counted = self._cards_seen[1]
        if self.variant == SINGLE:
            # The single model has no cards: none is counted, and none shown.
            self._cards_seen = (time.time(), {})
            return
        try:
            root, cards = self._tree()
            parts = [one for one in cards.values() if one["id"] != root["id"]]
            counted = {"cards": len(parts),
                       "cards_done": sum(1 for one in parts if one["status"] == "done")}
            # The contract in force in a line - its deliverables done of all,
            # its line and the version it holds since.
            contract = self.plan.get("contract") or {}
            if contract.get("goal") and getattr(self, "versions", None) is not None:
                deliverables = [one for one in parts if one.get("parent_id") == root["id"]
                                and one["status"] != "set_aside"]
                index = self.versions.index()
                since = in_force_since(index) or {}
                self._contract_seen = {
                    "goal": str(contract["goal"]), "branch": index.get("branch", ""),
                    "since": since.get("id", ""), "total": len(deliverables),
                    "done": sum(1 for one in deliverables if one["status"] == "done"),
                    "amended": bool(contract.get("instructions"))}
        except Exception:        # pragma: no cover - shown without them
            log.debug("could not count the cards of %s", self.id, exc_info=True)
        self._cards_seen = (time.time(), counted)

    def waiting_for(self) -> str:
        """What the run waits for you about, in a few words - "" when it
        does not."""
        ask = self._model_ask
        if ask is None or ask.get("status") != PENDING:
            return "your answer" if self._answers else ""
        if ask.get("trouble") == MODEL_STUCK:
            return f"the model - it has not answered for {_seconds_said(TEMPORARY_CAP_SECONDS)}"
        if ask.get("trouble") == MODEL_LIMIT:
            resets = float(ask.get("resets_ts") or 0)
            return "the model's usage limit" + (f" - it resets at {_clock(resets)}" if resets else "")
        return f"the model - {_clip(str(ask.get('reason') or ''), 80)}"

    def live_state(self) -> str:
        if not self.alive():
            return LIVE_INTERRUPTED if self.status == INTERRUPTED else LIVE_ENDED
        if self._answers:
            return LIVE_WAITING
        if self.pause_asked:
            return LIVE_PAUSED
        return LIVE_GOING

    def session(self, since: int = 0, calls: int = 0) -> Dict[str, Any]:
        """The live session, as the page polls it: the events after
        `since`, the model calls numbered after `calls`, where the run
        stands against its limits, its state, and the requests waiting for
        the user."""
        with self._events_lock:
            events = [one for one in self._events if one["seq"] > since]
            last = self._seq
        alive = self.alive()
        requests = list((self.plan.get("packages") or {}).get("requests") or [])
        pending = [_as_permission(one) for one in requests
                   if alive and one.get("status") == PENDING]
        pending += [_model_as_permission(one) for one in plan_waits(self.plan)
                    if alive and one.get("status") == PENDING]
        plan = self.plan
        used = self.used
        # Minutes to a tenth: the answer stays the same between looks a few
        # seconds apart, and so is not sent again (the server's ETag).
        return {"run": self.id, "events": events[-SESSION_BATCH:], "last": last,
                "calls": calls_after(plan, calls),
                "state": self.live_state(), "status": self.status,
                "pending": pending, "requests": _requests_in_brief(plan),
                "progress": {"stage": self.stage_now or plan.get("phase", ""),
                             "phase": plan.get("phase", ""),
                             "round": int(plan.get("round") or 0),
                             "rounds_done": len(plan.get("rounds") or []),
                             "cards": self._cards_seen[1].get("cards"),
                             "cards_done": self._cards_seen[1].get("cards_done"),
                             "used": {"minutes": round(self._minutes(), 1),
                                      "tokens": int(used.get("tokens_in") or 0)
                                      + int(used.get("tokens_out") or 0),
                                      "tokens_in": int(used.get("tokens_in") or 0),
                                      "tokens_out": int(used.get("tokens_out") or 0),
                                      "calls": int(used.get("calls") or 0)},
                             "limits": dict(self.limits),
                             "sound": self.sound,
                             "paused_minutes": round(self._paused_seconds() / 60, 1),
                             "activity": self.activity if alive else "",
                             "pause_asked": alive and self.pause_asked,
                             "held": bool(self._held_since),
                             "stopping": alive and self.stopping},
                "contract": self._contract_seen,
                "resumes_from": None}

    def _changed(self, action: str, paths: List[str], said: Dict[str, Any]) -> None:
        """What the agent did, recorded as ITS activity - so the heartbeats
        write it down as the agent's and draw nothing of the user's from it -
        and kept for the final review."""
        changed = self.plan.setdefault("changed_files", [])
        if not hasattr(self, "_round_writes"):
            self._round_writes = set()
        self._round_writes.update(_where(one) for one in paths)
        for path in paths:
            if path not in changed and "/runs/" not in path.replace("\\", "/"):
                changed.append(path)
        del changed[:-500]
        # In a granted folder: what the agent left each file as - so a
        # rollback puts back only what is still as it left it, and leaves a
        # file the user changed since.
        granted = [Path(one).resolve() for one in self.runtime.policy.extra_writable]
        if granted and paths:
            written = self.plan.setdefault("granted_writes", {})
            for path in paths:
                target = Path(path).resolve()
                if any(target == root or root in target.parents for root in granted):
                    written[str(target)] = sha_of_file(target) if target.is_file() else None
        raw = self.owner.raw_store
        # The Experience debugger's runs are kept apart from everything the
        # heartbeats read: not even as the agent's own activity.
        if raw is None or self.debug:
            return
        from ...common.events.models import ListenerEvent
        try:
            raw.add_event(ListenerEvent(
                event_type=f"agent.work.{action.replace(' ', '_')}",
                source="agent", application=APP_NAME,
                data={"by": "agent", "run": self.id,
                      "project": self.unit.get("name", ""),
                      "action": action, "paths": list(paths)[:50],
                      **({"path": paths[0]} if paths else {}),
                      **{k: v for k, v in said.items() if k in ("url", "query")}}))
        except Exception:        # pragma: no cover - a record, not the work
            log.debug("could not record the agent's activity", exc_info=True)

    def _version(self, kind: str, label: str, message: Optional[Dict[str, Any]] = None,
                 **details: Any) -> Optional[Dict[str, Any]]:
        """A version, saved at a key point: every file of the work's folder,
        the agent's files in granted folders, and where the run stands - its
        plan and the work's tree. A version that cannot be saved is noted,
        and the work goes on: it is a way back, not the work.

        ITS MESSAGE says what it did, as a commit does: a title (its label
        too), a few lines of the concrete changes, and the files added,
        modified and deleted since the version before it. `details`: what
        else names it - a round's number, what the reviewer found of it."""
        try:
            files, skipped = keep_files(self.versions, self.folder, self.id)
            granted = keep_granted(self.versions, self.plan.get("granted_writes") or {},
                                   self.runtime.policy.extra_writable)
            message = dict(message or commit_message(None, label))
            message["title"] = message.get("title") or commit_title(label)
            message["files"] = file_changes(self._parent_files(), files)
            label = message["title"]
            # THE VERSION'S SLIDES: where the whole work stands at it, drawn
            # by the builder with its tools (or, failing that, in words) and
            # told what this version did - DRAWN BEHIND THE WORK: the version
            # is saved now, the next step begins at once, and the slides are
            # added to it when they are drawn (`_SlidesBehind`).
            # The single model makes no slides: a call it was never asked for
            # would be spent on its account.
            alone = self.variant == SINGLE
            # A CONTRACT'S VERSION HAS NONE: no work to show yet.
            no_slides = kind in NO_SLIDES_FOR
            slides_why = ""
            if not (alone or no_slides) and self._model_shut:
                slides_why = _clip(f"the model could not be used - {self._model_shut}", 200)
            drawing = not (alone or no_slides or slides_why)
            # What the slides tell of the run, as it stands NOW - not as it
            # will when they are drawn.
            seen = self._slides_seen() if drawing else None
            slides = None
            plan = json.loads(json.dumps(self.plan, ensure_ascii=False, default=str))
            # The model calls are what happened, as the logs are: kept with
            # the run, whichever version it goes on from - not in each one.
            plan.pop("calls", None)
            for_user = list(getattr(self, "_for_user_new", None) or [])
            self._for_user_new = []
            state = self.versions.put_json({
                "plan": plan, "status": self.status, "final": self.final,
                "instruction": self.instruction, "slides": slides,
                "tree": keep_tree(self.versions, self.owner.db, self.scope)})
            root, cards = self._tree()
            parts = [one for one in cards.values() if one["id"] != root["id"]]
            todo = plan.get("todo") or {}
            summary = {"rounds": len(plan.get("rounds") or []),
                       "cards": None if alone else len(parts),
                       "cards_done": None if alone else
                       sum(1 for one in parts if one["status"] == "done"),
                       # The to-do list is written when a session ends
                       # unfinished; any other time one in the plan is an
                       # earlier session's.
                       "todo": len(todo.get("next") or []) if kind in UNFINISHED_ENDS
                       else 0,
                       "deck": bool(plan.get("deck")), "phase": plan.get("phase", ""),
                       # What this milestone found only the user can check
                       # or do; the run's whole list is in its plan.
                       "for_user": for_user,
                       "status": self.status,
                       # Made, or not made (the model could not be asked):
                       # either way the version is saved. The single model's
                       # have none.
                       "slides": "none" if alone else "skipped" if no_slides
                       else "drawing" if drawing else "not made",
                       **({"slides_why": slides_why} if slides_why else {}),
                       # What the whole run has spent up to this version -
                       # what went in and what came out, apart.
                       **self._tokens_so_far()}
            # The first version after an amendment carries it: the words and
            # what changed, for the tree to show on this line.
            amended, self._amended = self._amended, None
            entry = self.versions.save(kind, label, files, granted, skipped, state, summary,
                                       amended=amended, message=message, details=details)
        except Exception as exc:
            log.warning("could not save a version of %s", self.id, exc_info=True)
            self._note("version not saved", kind=kind, error=str(exc)[:300])
            return None
        self._note("version saved", version=entry["id"], kind=kind, label=label,
                   branch=entry["branch"])
        if drawing:
            self._behind.add({"entry": entry, "kind": kind, "label": label, "files": files,
                              "message": message, "seen": seen})
        return entry

    def _slides_seen(self, plan: Optional[Dict[str, Any]] = None,
                     status: Optional[str] = None) -> Dict[str, Any]:
        """What a version's slides tell of the run, taken when the version is
        saved - or, for one whose slides were never drawn, from the plan it
        kept (`plan`, `status`): its rounds so far, the reviewer's last
        findings, the goal, whether it is finished."""
        kept = plan is not None
        plan = self.plan if plan is None else plan
        rounds = [f"  {one.get('n')}. {one.get('card')}: {one.get('objective')} - "
                  f"{one.get('progress')}" for one in plan.get("rounds") or []]
        final = (plan.get("final_review") or {}).get("findings") or []
        last = "\n".join(f"  - ({one.get('type', '')}) {_plain(one.get('say', ''))}"
                         for one in ((plan.get("findings") or {}).get("findings") or [])[:12])
        findings = ""
        if final or last:
            findings = ("THE REVIEWER'S LAST FINDINGS:\n" + "\n".join(
                [f"  - ({one.get('severity', '')}) {_plain(one.get('say', ''))}"
                 for one in final if isinstance(one, dict)][:12]
                or [last]) + "\n\n")
        goal = str((plan.get("contract") or {}).get("goal") or "") if kept else ""
        if not kept:
            try:
                root, _cards = self._tree()
                goal = root.get("statement", "")
            except Exception:        # pragma: no cover - drawn without it
                goal = ""
        return {"rounds_done": "\n".join(rounds[-20:]) or "  (none yet)",
                "findings": findings, "goal": goal,
                "finished": (status if status is not None else self.status) == DONE}

    def _slides_left_behind(self) -> None:
        """A SESSION THAT ENDED BEFORE ITS VERSIONS' SLIDES WERE DRAWN - the
        computer went off, the application closed - leaves versions saved
        with none: continued, their slides are drawn first, behind the work,
        each from its own version's files and the plan it kept."""
        try:
            index = self.versions.index()
        except Exception:        # pragma: no cover - nothing to draw
            return
        for entry in index.get("versions") or []:
            if (entry.get("summary") or {}).get("slides") != "drawing":
                continue
            try:
                manifest = self.versions.manifest(entry["id"])
                state = self.versions.get_json(str(manifest.get("state") or "")) or {}
            except VersionError:
                continue
            self._note("slides drawn again", version=entry["id"])
            self._behind.add({"entry": entry, "kind": entry.get("kind") or "round",
                              "label": entry.get("label") or "",
                              "files": manifest.get("files") or {},
                              "message": entry.get("message") or {},
                              "seen": self._slides_seen(state.get("plan") or {},
                                                        state.get("status") or "")})

    def _draw_behind(self, job: Dict[str, Any]) -> None:
        """ONE VERSION'S SLIDES, drawn behind the work and added to it: from
        A COPY OF THE VERSION'S OWN FILES - the work's folder is already the
        next step's - after the version before it has its own."""
        entry = job["entry"]
        number, version_id = int(entry["n"]), entry["id"]
        parent = entry.get("parent") or ""
        copy = self._version_copy(version_id)
        # And the version before it on its line, to compare with: a copy of
        # its own, beside - only to read.
        before_root = self.slides_root / ".work" / f"before-{version_id}"
        before = (self._version_copy(parent, into=before_root / parent)
                  if parent and copy is not None else None)
        try:
            slides = self._progress_slides(job["kind"], job["label"], job["files"],
                                           job["message"], number=number,
                                           parent=parent, seen=job["seen"], work=copy,
                                           before_work=before,
                                           round_no=entry.get("round"))
        finally:
            import shutil
            if copy is not None:
                shutil.rmtree(copy, ignore_errors=True)
            shutil.rmtree(before_root, ignore_errors=True)
        why = ""
        if isinstance(slides, dict) and "not_made" in slides:
            why, slides = str(slides["not_made"]), None
        if not slides and not why:
            why = "nothing came of the call"
        try:
            self.versions.set_slides(version_id, slides or None,
                                     {"slides": "made" if slides else "not made",
                                      **({"slides_why": why} if why else {})})
        except Exception as exc:
            log.warning("could not add the slides of %s %s", self.id, version_id,
                        exc_info=True)
            self._note("slides not made", version=version_id,
                       why=_clip(f"they could not be kept: {exc}", 200))
            return
        self._note("slides drawn" if slides else "slides not made", version=version_id,
                   why=why)

    def _version_copy(self, version_id: str, into: Optional[Path] = None) -> Optional[Path]:
        """THE WORK AS IT WAS AT A VERSION, copied from the version's own
        files beside its slides folder, for them to be drawn from - None
        when it cannot be (too large, or a file missing): then they are
        drawn from the work's folder as it is."""
        try:
            manifest = self.versions.manifest(version_id)
            files = manifest.get("files") or {}
            if sum(int((one or {}).get("size") or 0) for one in files.values()) \
                    > VERSION_COPY_MOST_BYTES:
                return None
            copy = into or self.slides_root / ".work" / version_id
            import shutil
            shutil.rmtree(copy, ignore_errors=True)
            from ...common.agent_writes import mark_written
            mark_written(str(copy))
            for relative, one in files.items():
                parts = Path(str(relative)).parts
                if not parts or Path(relative).is_absolute() or ".." in parts:
                    continue
                target = copy.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.versions.get(str(one.get("sha") or "")))
            return copy
        except Exception:
            log.warning("could not copy version %s of %s for its slides", version_id,
                        self.id, exc_info=True)
            return None

    def _tokens_so_far(self) -> Dict[str, int]:
        """Tokens spent by every session of the run so far, in and out."""
        out = {"tokens_in": 0, "tokens_out": 0}
        for one in self.plan.get("sessions") or []:
            used = one.get("used") or {}
            for name in out:
                out[name] += int(used.get(name) or 0)
        return out

    def _parent_files(self) -> Dict[str, Any]:
        """The files of the version a new one comes after - none for the
        first, or one that cannot be read."""
        head = self.versions.index().get("head") or ""
        try:
            return (self.versions.manifest(head).get("files") or {}) if head else {}
        except VersionError:
            return {}

    def _previous_slides(self, parent: Optional[str] = None
                         ) -> Tuple[str, Optional[Dict[str, Any]], Dict[str, Any]]:
        """The version this one comes after - `parent`, or the head: its
        id, its slides and its files - "" and nothing for the first."""
        head = parent if parent is not None else self.versions.index().get("head") or ""
        if not head:
            return "", None, {}
        try:
            manifest = self.versions.manifest(head)
            state = self.versions.get_json(str(manifest.get("state") or "")) or {}
        except VersionError:
            return head, None, {}
        slides = state.get("slides") if isinstance(state, dict) else None
        return head, slides if isinstance(slides, dict) else None, manifest.get("files") or {}

    def _progress_slides(self, kind: str, label: str, files: Dict[str, Dict[str, Any]],
                         message: Optional[Dict[str, Any]] = None,
                         number: Optional[int] = None, parent: Optional[str] = None,
                         seen: Optional[Dict[str, Any]] = None,
                         work: Optional[Path] = None,
                         before_work: Optional[Path] = None,
                         round_no: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """THE PROGRESS SLIDES OF A VERSION: where the whole work stands at
        it - what is done with its evidence, what is new since the version
        before, the results so far, what remains, the open problems and the
        next steps - SHOWN, as a presentation is: the builder draws the
        figures with its tools (`_drawn_slides`) and lays them out as 16:9
        slides from a fixed set of layouts, given the deck of the version
        before to build on.

        When they cannot be drawn - the tools or the figures failed, or the
        run has no room left for new work - they are made in WORDS, as they
        were before, in one call from the reserve, and say so. None when not
        even that could be made - stopped, out of room, the model
        unavailable, or nothing said; the version is saved anyway, marked
        so. Every call counted in the run's use and kept in its transcript,
        as the slides of that version.

        Drawn behind the work (`_draw_behind`): `parent` the version it came
        after, `seen` the run as it stood when it was saved, `work` a copy
        of its files to draw from."""
        if self._model_shut:
            # Not even asked: the model could not be used.
            return {"not_made": _clip(f"the model could not be used - {self._model_shut}", 200)}
        try:
            previous, before, before_files = self._previous_slides(parent)
            number = number or len(self.versions.index().get("versions") or []) + 1
            seen = seen or self._slides_seen()
            added = sorted(set(files) - set(before_files))
            removed = sorted(set(before_files) - set(files))
            changed = sorted(one for one in set(files) & set(before_files)
                             if (files[one] or {}).get("sha")
                             != (before_files[one] or {}).get("sha"))
            listed = ([f"  added: {one}" for one in added[:60]]
                      + [f"  changed: {one}" for one in changed[:60]]
                      + [f"  removed: {one}" for one in removed[:60]])
            shown_before = ({k: v for k, v in before.items() if k not in ("figures", "dropped")}
                            if before else None)
            message = message or {}
            change = "\n".join([f"  {message.get('title') or label}"]
                               + [f"    - {one}" for one in message.get("body") or []])
            # Its id, as everything else names it, and its round.
            version_name = f"v{number}"
            round_line = (f"It is ROUND {round_no} of the work: the title slide says "
                          f"\"{version_name} - round {round_no}\".\n" if round_no else "")
            record = {"version": version_name, "kind": kind, "label": label,
                      "round_line": round_line,
                      "name": f"{version_name} - round {round_no}" if round_no else version_name,
                      "change": change,
                      "previous": previous or "the start",
                      "files": "\n".join(listed) or "  (none)",
                      "rounds_done": seen["rounds_done"],
                      "findings": seen["findings"],
                      "before": json.dumps(shown_before, ensure_ascii=False)[:12000]
                      if shown_before else "(none - no version before this one has slides)"}
            base = {"kind": "progress", "version": version_name, "version_kind": kind,
                    "label": label, "since": previous, "goal": seen["goal"],
                    "finished": bool(seen["finished"])}
        except Exception as exc:
            log.warning("could not make the slides of a version of %s", self.id,
                        exc_info=True)
            return {"not_made": _clip(f"{type(exc).__name__}: {exc}", 200)}
        folder = self.slides_root / f"v{number}"
        # What a figure is kept as - its path in the work's folder, its
        # bytes in the version's store - and what was left out, and why.
        figures: Dict[str, Dict[str, Any]] = {}
        dropped: List[str] = []

        def keep(path: str) -> str:
            return self._slide_figure(path, folder, figures, dropped)

        self._part = f"slides of v{number}"
        if threading.current_thread() is self._thread:
            self.activity = f"worker: the slides of version v{number}"
        try:
            try:
                said, why = self._drawn_slides(record, folder, previous, keep, work=work,
                                               before_work=before_work)
            except Exception as exc:
                log.warning("could not draw the slides of a version of %s", self.id,
                            exc_info=True)
                said, why = None, _clip(f"{type(exc).__name__}: {exc}", 200)
            if said is None and not why:
                why = "the builder said nothing"
            elif said is None:
                # IN WORDS, from the reserve: as the slides were before.
                self._note("slides not drawn", version=f"v{number}", why=why)
                messages = [{"role": "user", "content": self._brief() + "\n\n"
                             + MAKE_TEXT_SLIDES.format(why=why, **record)}]
                answer = self._ask(self._system(), messages, self.tools, writing=False,
                                   rounds=1, work=False)
                said = self._worded_slides(answer, keep)
                if said is not None:
                    said["fallback"] = why
                else:
                    why += "; and none came in words either"
        except Exception as exc:
            log.warning("could not make the slides of a version of %s", self.id,
                        exc_info=True)
            return {"not_made": _clip(f"{type(exc).__name__}: {exc}", 200)}
        finally:
            self._part = ""
            # A folder nothing was drawn in is not left behind.
            try:
                folder.rmdir()
            except OSError:
                pass
        if said is None:
            # WHY THERE ARE NONE, kept with the version and shown with it.
            return {"not_made": why or "nothing came of the call"}
        if dropped:
            said["dropped"] = dropped[:30]
            self._note("slide images left out", version=f"v{number}", images=dropped[:10])
        said.update(base, figures=figures)
        return said

    def _drawn_slides(self, record: Dict[str, Any], folder: Path, previous: str,
                      keep: Any, work: Optional[Path] = None,
                      before_work: Optional[Path] = None
                      ) -> Tuple[Optional[Dict[str, Any]], str]:
        """THE SLIDES, DRAWN: a few rounds of tools in which the builder
        draws its figures into the version's slides folder, then the SLIDE
        SPEC it answers with - each slide one of the deck's layouts, each
        image it names kept through `keep` (or left out). The deck, and ""
        - or None and why they could not be drawn, for the slides in words;
        None and "" when the builder said nothing at all, which words would
        not mend.

        Spent like the work: from what is left for new work, never from the
        reserve kept for the wrap-up - a run with no room left gets its
        slides in words, from the reserve, in one call."""
        from .deck import place_images, shows_something, visual_spec
        if not self._going(work=True, quiet=True):
            return None, "the run had no room left for new work"
        tools = self._slides_tools(folder, work, before_work)
        before_folder = self.slides_root / previous if previous else None
        packages = (" - or install what drawing needs with install_packages"
                    if tools.install is not None else "")
        # The version's own files, copied: the work's folder is already the
        # next step's.
        before_note = (BEFORE_COPY_NOTE.format(previous=record["previous"], before=str(before_work))
                       if before_work is not None else "")
        work_note = (VERSION_COPY_NOTE.format(version=record["version"], work=str(work),
                                              live=str(self.folder), before_note=before_note)
                     if work is not None else "")
        prompt = MAKE_PROGRESS_SLIDES.format(
            folder=str(folder), work=str(work or self.folder), packages=packages,
            work_note=work_note,
            previous_folder=str(before_folder) if before_folder is not None
            and before_folder.is_dir() else "(none)",
            rounds=SLIDES_TOOL_ROUNDS, language=self.language, **record)
        messages = [{"role": "user", "content": self._brief() + "\n\n" + prompt}]
        said = self._ask(self._system(), messages, tools, writing=True,
                         rounds=SLIDES_TOOL_ROUNDS, work=True, quiet=True)
        if not said:
            # Cut off before it answered: stopped, or out of room.
            return None, "the run was stopped or ran out of room while they were drawn"
        said = {k: v for k, v in said.items() if k not in ("written_by", "interrupted")}
        if not said:
            # IT ANSWERED, BUT NOT IN JSON THAT READS - a brace too many in a
            # deck of fifteen slides: sent back once to be corrected, with
            # where it fails. Not corrected, the slides are made in words.
            problem = json_problem(getattr(self._local_state(), "answer", ""))
            if not problem:
                return None, "its answer held no slides"
            self._note("slides not valid JSON", why=problem)
            messages.append({"role": "user", "content": FIX_JSON.format(problem=problem)})
            fixed = self._ask(self._system(), messages, tools, writing=False, rounds=1,
                              work=True, quiet=True)
            said = {k: v for k, v in (fixed or {}).items()
                    if k not in ("written_by", "interrupted")}
            if not said:
                return None, (f"its answer was not valid JSON ({problem}), "
                              "and not when it was asked to correct it")
        if isinstance(said.get("slides"), list):
            spec = visual_spec(said)
            slides = place_images(spec, keep)
            if not shows_something(slides):
                return None, "no figure could be shown"
            return {"format": "drawn", "title": spec["title"], "subtitle": spec["subtitle"],
                    "slides": slides, "folder": folder.relative_to(self.folder).as_posix()}, ""
        # Answered in words - the slides as they were before: kept so.
        worded = self._worded_slides(said, keep)
        return (worded, "") if worded is not None else (None, "")

    def _worded_slides(self, said: Dict[str, Any], keep: Any) -> Optional[Dict[str, Any]]:
        """Slides in words - the title, the state, what is done, what is
        new, the results, what remains - as the builder said them; None if
        it said none of it. A result's figure is kept through `keep`, or
        left out: never shown broken."""
        said = {k: v for k, v in (said or {}).items() if k not in ("written_by", "interrupted")}
        if not any(said.get(one) for one in ("title", "summary", "done", "results")):
            return None
        for result in said.get("results") or []:
            if isinstance(result, dict) and result.get("figure"):
                result["figure"] = keep(str(result["figure"]))
        said["format"] = "words"
        return said

    def _slides_tools(self, folder: Path, work: Optional[Path] = None,
                      before: Optional[Path] = None) -> WorkTools:
        """THE TOOLS A VERSION'S SLIDES ARE DRAWN WITH: the builder's own,
        under a policy NARROWER than the work's - it writes in the version's
        slides folder and NOWHERE ELSE (not the work's folder, not a granted
        one: those it may only read, as it always could), and everything
        else as the work's policy has it: no network from code it runs, the
        same sealed, closed and protected folders, the same guard and
        operating-system confinement around that code. No web, no cards: only
        `SLIDES_TOOLS`."""
        policy = self.runtime.policy
        self.runtime.mkdir(folder)
        from ...common.agent_writes import mark_written
        mark_written(str(folder))
        # The work's folder and the granted ones, NAMED - even under a wider
        # readable root (the home). An Experience debugger way's folder is
        # sealed from the other ways: what it inherits from above stops at
        # it, and only a grant of its own opens it. Folded into the home's,
        # the slides' code was refused every file of the work it drew.
        readable = list(policy.readable_paths)
        for one in [self.folder, *policy.extra_writable]:
            if not any(Path(one).resolve() == Path(held).resolve() for held in readable):
                readable.append(Path(one))
        # And THE COPY OF THE VERSION it is drawn from (`_version_copy`), when
        # there is one: the work's own code, run there to show it - an app
        # that makes its folder or its database as it starts - writes in a
        # copy thrown away once the slides are drawn, never in the work.
        # And the copy of THE VERSION BEFORE, to compare with: to read only.
        if before is not None:
            readable.append(Path(before))
        narrowed = replace(policy, writable_root=folder.resolve(),
                           extra_writable=[work.resolve()] if work is not None else [],
                           readable_paths=readable)
        runtime = SandboxRuntime(
            narrowed, platform=getattr(self.runtime, "platform", None),
            sandbox_enabled=getattr(self.runtime, "sandbox_enabled", True),
            observability=getattr(self.runtime, "observability", None),
            bus=getattr(self.runtime, "bus", None), tracer=getattr(self.runtime, "tracer", None))
        tools = WorkTools(runtime, log=self._tool_used, changed=self._changed,
                          install=self._install)
        tools.only = SLIDES_TOOLS
        tools.locked = [one for one in self.tools.locked if one != self.slides_root]
        tools.packages_dir = self.packages_dir
        return tools

    def _slide_figure(self, path: str, folder: Path, figures: Dict[str, Dict[str, Any]],
                      dropped: List[str]) -> str:
        """An image a slide names, KEPT WITH THE VERSION: what it is kept
        as - its path inside the work's folder - or "" with why it was left
        out. Only an image file inside the work's folder (its outputs, the
        version's slides folder) that the work's policy lets it read: not
        the run logs, not the saved versions, the installed packages or an
        older run's session; at most `MOST_FIGURES`, each at most
        `MOST_FIGURE_BYTES`. A relative path is the slides folder's first -
        where its code ran - then the work's folder's."""
        from ...runtime.filesystem import validate_read_path
        from .deck import IMAGE_TYPES
        raw = str(path or "").strip()
        if not raw:
            return ""
        target = Path(raw).expanduser()
        tried = [target] if target.is_absolute() else [folder / target, self.folder / target]
        found = next((one for one in tried if one.is_file()), None)
        why, key, whole = "", "", None
        if found is None:
            why = "not found"
        else:
            try:
                whole = found.resolve()
                parts = whole.relative_to(self.folder.resolve()).parts
            except (OSError, ValueError):
                parts, why = (), "not inside the work's folder"
            if not why:
                key = "/".join(parts)
                if whole.suffix.lower() not in IMAGE_TYPES:
                    why = "not an image"
                elif parts[:1] == (LOGS_DIR,) or (
                        len(parts) > 2 and parts[0] == "runs"
                        and parts[2] in (VERSIONS_DIR, PACKAGES_DIR, SESSION_DIR)):
                    why = "not a file of the work"
                elif whole.stat().st_size > MOST_FIGURE_BYTES:
                    why = "too large to show"
                elif key not in figures and len(figures) >= MOST_FIGURES:
                    why = "too many figures"
                else:
                    try:
                        validate_read_path(str(whole), self.runtime.policy)
                    except SecurityViolation:
                        why = "not readable"
        if why:
            dropped.append(f"{raw}: {why}")
            return ""
        if key not in figures:
            sha, size = self.versions.put_file(whole)
            figures[key] = {"sha": sha, "size": size}
        return key

    def _end_version(self) -> None:
        """The version a session ends with - finished, paused at a limit,
        stopped, or ended otherwise - named for how it ended."""
        try:
            self.plan["active_seconds_ended"] = round(
                self._active_before() + self._minutes() * 60, 1)
        except Exception:        # pragma: no cover - a record, not the work
            log.debug("could not carry the run's time", exc_info=True)
        reason = self.stop_reason or ""
        if self.status == DONE:
            kind = "finished"
        elif reason == "stopped":
            kind = "stopped"
        elif reason.endswith("limit"):
            kind = "limit reached"
        elif self.status == NOT_FINISHED:
            kind = "paused"
        else:
            kind = "ended"
        said = {"finished": "Finish", "stopped": "Stop", "limit reached": "Pause",
                "paused": "Pause", "ended": "End"}[kind]
        self._version(kind, f"{self.status}{' - ' + reason if reason else ''}",
                      message=commit_message(None, f"{said}: {reason or self.status}",
                                             [f"status: {self.status}"]
                                             + ([f"why: {reason}"] if reason else [])))

    # -- limits --------------------------------------------------------------------------
    def _going(self, work: bool = True, quiet: bool = False) -> bool:
        """Whether to go on. New work stops while the reserve of each limit
        is still left; the wrap-up may spend it. Stopping by the user ends
        the work; stopping now ends everything.

        `quiet`: only asked - for a part that is not the work (a version's
        slides) - so the run is not taken to have stopped for the reason
        found: a run that finished and drew its last slides from the
        reserve has not stopped at a limit."""
        if self.stop_now:
            if not quiet:
                self.stop_reason = self.stop_reason or "stopped"
            return False
        if work and self.stopping:
            if not quiet:
                self.stop_reason = self.stop_reason or "stopped"
            return False
        # Every call going now - the slides' and the reviewer's at once.
        inflight = list((getattr(self, "_inflight", None) or {}).values())
        tokens_in = int(self.used.get("tokens_in") or 0) + sum(
            int(one.get("tokens_in") or 0) for one in inflight)
        tokens_out = int(self.used.get("tokens_out") or 0) + sum(
            int(one.get("tokens_out") or 0) for one in inflight)
        spent = {"minutes": self._minutes(), "tokens_in": tokens_in,
                 "tokens_out": tokens_out, TOTAL_TOKENS: tokens_in + tokens_out,
                 "calls": int(self.used.get("calls") or 0)}
        reached = limit_reached(self.limits, spent, reserve=work)
        if reached:
            if not quiet:
                self.stop_reason = self.stop_reason or f"the {LIMIT_NAMES[reached]} limit"
            return False
        return True

    # -- the record the agents read ----------------------------------------------------------
    def _open_cards(self, ids: List[str]) -> Dict[str, Any]:
        """Cards of the work's tree, or of the project's record."""
        said = self.writer.open_cards(self.work_unit, ids)
        missing = [one["id"] for one in said["cards"] if one.get("error")]
        if missing and self.live and self.live_writer is not None:
            more = {one["id"]: one for one in
                    self.live_writer.open_cards(self.live, missing)["cards"]}
            said["cards"] = [more.get(one["id"], one) if one.get("error") else one
                             for one in said["cards"]]
        return said

    def _find(self, query: str, kind: str, only_project: bool) -> Dict[str, Any]:
        if self.retriever is None:
            return {"error": "no record to search"}
        profile = {"text": query, "kinds": [kind] if kind else [],
                   "tasks": self.project_tasks}
        if only_project:
            profile["only_tasks"] = self.project_tasks or ["-"]
        return compact_experience(self.retriever.find(profile))

    # -- what the reviewer was given for a card, kept ---------------------------------
    def experience_given(self, key: str) -> Optional[Dict[str, Any]]:
        """What the reviewer was given of the user's past work for this card
        and use the first time (`Review`), or None."""
        if not key:
            return None
        rows = self.owner.db.query("SELECT found FROM work_run_experience "
                                   "WHERE run_id = ? AND key = ?", (self.id, key))
        if not rows:
            return None
        try:
            said = json.loads(rows[0]["found"] or "{}")
        except ValueError:
            return None
        return said if isinstance(said, dict) else None

    def keep_experience_given(self, key: str, found: Dict[str, Any]) -> None:
        if not key:
            return
        try:
            self.owner.db.execute(
                "INSERT OR IGNORE INTO work_run_experience (run_id, key, found, created_at) "
                "VALUES (?, ?, ?, ?)",
                (self.id, key, json.dumps(found, ensure_ascii=False, default=str), _now()))
        except Exception:                      # pragma: no cover - retrieved again then
            log.debug("could not keep what the reviewer was given", exc_info=True)

    def _unreadable(self) -> Optional[List[str]]:
        """Every place this run may not read, worked out once a run - None
        when they are too many to list (no reading tools of the client's)."""
        if not hasattr(self, "_unreadable_list"):
            from ...runtime.filesystem import unreadable_for
            self._unreadable_list = unreadable_for(self.runtime.policy)
        return self._unreadable_list

    @property
    def checks_folder(self) -> Path:
        """Where the reviewer and the tester write what trying the work
        makes: in the run's own folder, never the work."""
        return self.folder / "runs" / self.id / "checks"

    def _checking_runtime(self) -> SandboxRuntime:
        """THE REVIEWER'S AND THE TESTER'S SANDBOX: the code they run to try
        the work writes in the run's checks folder AND NOWHERE ELSE - the
        work's folder and every folder granted it are read-only to them, as
        the system confines it, not as they are asked. Everything else as
        the work's policy has it (no network, the same sealed, closed and
        protected folders)."""
        if getattr(self, "_checking", None) is not None:
            return self._checking
        policy = self.runtime.policy
        folder = self.checks_folder
        self.runtime.mkdir(folder)
        from ...common.agent_writes import mark_written
        mark_written(str(folder))
        readable = list(policy.readable_paths)
        for one in [self.folder, *policy.extra_writable]:
            if not any(Path(one).resolve() == Path(held).resolve() for held in readable):
                readable.append(Path(one))
        narrowed = replace(policy, writable_root=folder.resolve(), extra_writable=[],
                           readable_paths=readable)
        self._checking = SandboxRuntime(
            narrowed, platform=getattr(self.runtime, "platform", None),
            sandbox_enabled=getattr(self.runtime, "sandbox_enabled", True),
            observability=getattr(self.runtime, "observability", None),
            bus=getattr(self.runtime, "bus", None), tracer=getattr(self.runtime, "tracer", None))
        return self._checking

    def _in_english(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """A question to the user's record, its words said as the English
        words the work would be recorded under (SEARCH_WORDS) - asked once
        for each text in a run. Each question is ranked on its own: a stage
        handed over for another card is not held against this one - each
        reviewer's call is new, and holds none of what an earlier one was
        given."""
        asked = dict(profile)
        # THE WORK'S GOAL beside each question, as English words too: a
        # stage from a project like this one is ranked above one that only
        # shares its words (retrieve.LIKENESS["like_this"]).
        goal = str(self._contract_profile().get("text") or "").strip()
        if goal:
            asked["goal_text"] = " ".join(self._english_words(goal)) or goal
        text = str(profile.get("text") or "").strip()
        if not text:
            return asked
        words = self._english_words(text)
        if words:
            asked["text"] = " ".join(words)
            asked["said_as"] = text
        return asked

    def _english_words(self, text: str) -> List[str]:
        """A text as the English words the work would be recorded under -
        asked of the model once for each text in a run."""
        if not hasattr(self, "_english"):
            self._english = {}
        if text not in self._english:
            words: List[str] = []
            try:
                said = self._ask(SEARCH_WORDS + "\n\n" + zone_rule(),
                                 [{"role": "user", "content": text[:6000]}],
                                 self.review_tools, writing=False, rounds=1,
                                 work=False, quiet=True, who="reviewer",
                                 language=in_english("the search words"))
                words = [" ".join(str(one).split()) for one in said.get("keywords") or []
                         if str(one).strip()][:15]
            except Exception:                       # pragma: no cover - its own words
                log.warning("the search words could not be asked for", exc_info=True)
            self._english[text] = words
        return self._english[text]

    def _open_stages(self, ids: List[str]) -> Dict[str, Any]:
        """Past stages of the user's record, each opened to its key
        information (tools.stage_key_info) - what a stage's line in the
        index points at."""
        from ...heartbeat.experience.stages import depth_of, level_of
        store = self.retriever.store
        cards = {card["id"]: card for card in store.cards()}
        out = []
        for one in ids:
            stage = store.stage(one)
            if stage is None:
                out.append({"id": one, "error": "no such stage"})
                continue
            links = [{"to": edge["target"] if edge["source"] == one else edge["source"],
                      "kind": edge["kind"],
                      "direction": "from it" if edge["source"] == one else "to it"}
                     for edge in store.edges([one])]
            card = cards.get(stage.get("card_id") or "")
            if card is not None:
                card = dict(card, level=level_of(depth_of(card, cards)))
            out.append(stage_key_info(stage, card, links))
        return {"stages": out}

    def _find_in_project(self, query: str, kind: str) -> Dict[str, Any]:
        """The work agent searches this project's record only."""
        return self._find(query, kind, only_project=True)

    def _find_anywhere(self, query: str, kind: str) -> Dict[str, Any]:
        """The reviewer searches all of the user's work."""
        return self._find(query, kind, only_project=False)

    # -- asking ------------------------------------------------------------------------------
    def _spent(self) -> int:
        return int(self.used.get("tokens_in") or 0) + int(self.used.get("tokens_out") or 0)

    def _turn(self, who: str, response: Any, before: int) -> None:
        """A model turn, as a line of the live session: the tools it calls,
        or what it answered - and what it cost."""
        calls = getattr(response, "tool_calls", None) or []
        tokens = self._spent() - before
        usage = getattr(response, "usage", None)
        # What went in, whole - its cached part too (see `input_of`).
        split = {"tokens_in": input_of(usage)["tokens_in"],
                 "tokens_out": int(getattr(usage, "output_tokens", 0) or 0)}
        ran = getattr(response, "tool_exchanges", None) or []
        if calls:
            names = [call.name for call in calls]
            text = (f"Calls {len(calls)} tool{'s' if len(calls) != 1 else ''}: "
                    + ", ".join(names[:6]) + ("…" if len(names) > 6 else ""))
        else:
            text = "Answers - " + _gist(getattr(response, "text", "") or "")
        if ran:
            names = [str(one.get("name", "")) for one in ran]
            text = (f"Ran {len(ran)} tool{'s' if len(ran) != 1 else ''} "
                    f"({', '.join(names[:6])}{'…' if len(names) > 6 else ''}), then "
                    + text[0].lower() + text[1:])
        self._event(who, "turn", text, tokens=tokens, **split,
                    model=getattr(response, "model", "") or "",
                    full=answer_text(response))

    def _ask(self, system: str, messages: List[Dict[str, Any]], tools: WorkTools,
             writing: bool, rounds: int = TOOL_ROUNDS, work: bool = True,
             pause: str = "", who: str = "worker", quiet: bool = False,
             language: Any = WORK_LANGUAGE) -> Dict[str, Any]:
        """One answer, after as many rounds of tools as it needs: the JSON
        object it ends with. The messages keep the whole exchange, so the
        same context can be asked again.

        When the budget for work runs out part-way, and `pause` says how to
        stop, the agent is asked once more - from the reserve, with no tools
        - for what it did so far; that answer is marked interrupted."""
        from ...heartbeat.experience.store import count_use
        response = None
        interrupted = False
        for round_number in range(rounds):
            # SAFE POINT: before a model call.
            self._hold(f"before the {who}'s next model call")
            if not self._going(work=work, quiet=quiet):
                if pause and len(messages) > 1 and self._going(work=False):
                    last = messages[-1]
                    if isinstance(last.get("content"), list):
                        last["content"].append({"type": "text", "text": pause})
                    else:
                        messages.append({"role": "user", "content": pause})
                    interrupted = True
                    self.activity = f"{who}: saying what it did so far"
                    spent = self._spent()
                    response = self._call(who, ModelRequest(
                        messages=messages, system=system, tools=None,
                        max_tokens=8000, task="work_run", metadata={"run": self.id},
                        language=language))
                    self._turn(who, response, spent)
                    break
                return {}
            last = round_number == rounds - 1
            self.activity = f"{who}: thinking - {self.stage_now or 'working'}"
            spent = self._spent()
            specs = None if last else tools.specs(writing=writing)
            response = self._call(who, ModelRequest(
                messages=messages, system=system, tools=specs,
                max_tokens=8000, task="work_run", metadata={"run": self.id},
                language=language,
                tool_runner=self._tool_runner(tools, specs, who, work, quiet,
                                              rounds - 1 - round_number) if specs else None,
                cancelled=lambda: self.stop_now, on_usage=self._usage_so_far))
            self._turn(who, response, spent)
            calls = getattr(response, "tool_calls", None) or []
            if not calls:
                break
            messages.append({"role": "assistant",
                             "content": getattr(response, "raw_content", None)
                             or getattr(response, "text", "")})
            results = []
            for call in calls:
                # SAFE POINT: before a tool call. Stopped while held, the
                # rest of this turn's calls are not run - each still gets
                # its answer, so the exchange stays whole.
                self._hold(f"before {call.name}")
                if self.stop_now or (work and self.stopping):
                    said: Any = {"error": "not run: the user stopped the run"}
                else:
                    self.activity = f"{who}: {call.name} {_short_args(call.name, call.arguments)}"
                    said = tools.call(call.name, call.arguments or {})
                # A picture it read goes as a picture (models/vision.py).
                from ...models.vision import tool_content
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": tool_content(said)})
            if round_number == rounds - 2:
                results.append({"type": "text", "text": "That is all the room for "
                                "tools: answer now, with the JSON object."})
            messages.append({"role": "user", "content": results})
            self.owner.save(self)
        if response is None:
            return {}
        text = getattr(response, "text", "") or ""
        # The tools it ran during the call, kept with what it said: asked
        # again in the same context (a deck sent back), it knows what it did.
        messages.append({"role": "assistant",
                         "content": _exchanges_text(response) + text})
        said = _as_json(text)
        said["written_by"] = {"model": getattr(response, "model", ""),
                              "provider": getattr(response, "provider", "")}
        if interrupted:
            said["interrupted"] = True
        return said

    #: The most tool calls one call that runs its tools itself may make, for
    #: each round of tools the run's own loop would have given it.
    NATIVE_CALLS_PER_ROUND = 3

    def _tool_runner(self, tools: WorkTools, specs: List[Any], who: str, work: bool,
                     quiet: bool, rounds_left: int) -> Any:
        """A TOOL CALLED NATIVELY, DURING THE CALL - Claude Code, over MCP -
        run by the run as its own loop runs one: before it, the safe point
        (paused, it waits there); not run when the run was stopped, has no
        room left, or has used the room for tools this call has; said in the
        live session; run by the same tools, sandbox and guard."""
        offered = {str(spec.name) for spec in specs or []}
        most = max(1, rounds_left) * self.NATIVE_CALLS_PER_ROUND
        made = {"n": 0}

        def run(name: str, arguments: Dict[str, Any]) -> Any:
            if name not in offered:
                return {"error": f"no tool {name} here"}
            self._hold(f"before {name}")
            if self.stop_now or (work and self.stopping):
                return {"error": "not run: the user stopped the run - answer now, "
                                 "with the JSON object"}
            if not self._going(work=work, quiet=True):
                return {"error": "not run: the run has no room left - answer now, "
                                 "with the JSON object, from what you have"}
            made["n"] += 1
            if made["n"] > most:
                return {"error": "That is all the room for tools: answer now, "
                                 "with the JSON object."}
            self.activity = f"{who}: {name} {_short_args(name, arguments)}"
            said = tools.call(name, arguments or {})
            self.owner.save(self)
            return said
        return run

    def _usage_so_far(self, usage: Any) -> None:
        """What the call going now has used so far (`_inflight`)."""
        read = input_of(usage)
        self._inflight[threading.get_ident()] = {
            "tokens_in": int(read["tokens_in"] or 0),
            "tokens_out": int(getattr(usage, "output_tokens", 0) or 0)}

    def _call(self, who: str, request: ModelRequest) -> Any:
        """ONE MODEL CALL - and, when it fails, what the failure asks for
        (`model_trouble`):

        FOR THE MOMENT - it timed out, or its provider was unavailable,
        overloaded or out of reach: made again after CALL_RETRY_WAITS, then
        CALL_WAIT_LONGER - the last again and again - until it answers. The
        run goes on; each attempt is recorded in the transcript as a call of
        its own, and said in the live session.

        A USAGE LIMIT, or WHAT ONLY YOU CAN MEND: the run waits, says so,
        and says why and where to mend it (`_wait_for_model`) - and goes on
        once the model answers: when you say to try again, or when it looks
        again by itself. Ended by you, or stopped while it waits, no other
        call is made in this session (`ModelShut`).

        Anything else ends the run, as before. Stopping now ends any wait."""
        # A call with tools may read with the client's own reading tools -
        # denied every place this run may not read.
        if request.tools and request.native_read is None:
            unreadable = self._unreadable()
            if unreadable is not None:
                request = replace(request, native_read=unreadable)
        # A WAY OR A TRY OF THE WORK DEBUGGER, kept apart from the others:
        # every call's reading stays sealed - no model that reads by itself
        # where nothing keeps it out (models/manager.READS_UNCONFINED).
        if isinstance(self.unit.get("debug"), dict) and not request.sealed_reading:
            request = replace(request, sealed_reading=True)
        attempt = 0
        # Since when this call has failed for the moment; and whether you
        # were already asked about it - then a failure waits for you again.
        failing_since = 0.0
        stuck = False
        while True:
            if self._model_shut:
                raise ModelShut(self._model_shut)
            attempt += 1
            try:
                response = self._call_once(who, request)
            except Exception as exc:
                if self.stop_now:
                    raise
                trouble = model_trouble(exc)
                if trouble is None:
                    raise
                if trouble == MODEL_TEMPORARY:
                    failing_since = failing_since or time.time()
                    if stuck or time.time() - failing_since >= TEMPORARY_CAP_SECONDS:
                        self._wait_for_model(MODEL_STUCK, exc)
                        stuck, attempt = True, 0
                        continue
                    waits = CALL_RETRY_WAITS + CALL_WAIT_LONGER
                    wait = waits[min(attempt - 1, len(waits) - 1)]
                    timed_out = any(one in str(exc).lower() for one in
                                    ("did not answer within", "timed out", "timeout"))
                    self._event("run", "retry",
                                f"model call {'timed out' if timed_out else 'failed'} - "
                                f"trying again in {_seconds_said(wait)} (attempt {attempt + 1})",
                                error=_clip(str(exc), 300))
                    log.info("%s: a model call failed (%s) - trying again in %ss",
                             self.id, exc, wait)
                    if not self._model_pause(wait, timed_out):
                        raise ModelShut(self._stopped_waiting()) from exc
                    continue
                self._wait_for_model(trouble, exc)
                attempt = 0
                continue
            ask = self._model_ask
            if ask is not None and ask.get("status") == PENDING:
                self._settle_model(ask, CONTINUED, "the model answered again")
            return response

    def _model_pause(self, seconds: float, timed_out: bool) -> bool:
        """Wait before a call is made again - False when the run was
        stopped meanwhile. The wait is not the run's time."""
        self.activity = (f"the model {'did not answer' if timed_out else 'is unavailable for now'}"
                         f" - trying again at {_clock(time.time() + seconds)}")
        self._waiting_begins()
        try:
            end = time.time() + max(0.0, float(seconds))
            while time.time() < end:
                if self.stop_now or self.stopping:
                    return False
                time.sleep(min(WAIT_STEP, max(0.0, end - time.time())))
            return not (self.stop_now or self.stopping)
        finally:
            self._waiting_ends()
            self.activity = ""

    def _waiting_begins(self) -> None:
        with self._model_lock:
            if not self._model_waiters:
                self._model_waiting_since = time.time()
            self._model_waiters += 1

    def _waiting_ends(self) -> None:
        """The time waited on the model, counted once - however many
        threads waited together - and not against the run's minutes."""
        with self._model_lock:
            self._model_waiters = max(0, self._model_waiters - 1)
            if not self._model_waiters and self._model_waiting_since:
                self.waited += time.time() - self._model_waiting_since
                self._model_waiting_since = 0.0

    def _stopped_waiting(self) -> str:
        self.stop_reason = self.stop_reason or "stopped"
        self._model_shut = self._model_shut or "the run was stopped while it waited for the model"
        return self._model_shut

    def _wait_for_model(self, trouble: str, exc: BaseException) -> None:
        """WAITING ON THE MODEL - a usage limit, or what only you can mend.
        The run says it is waiting for you, why, and what to do: where to
        sign in again, when the limit resets. Returns when the call should
        be made again - you said to, or it is time to look again (a minute
        after the limit resets; every ten minutes when it did not say when;
        now and then for the rest) - the wait going on until the model
        answers. Raises ModelShut when you ended the run, or it was
        stopped."""
        with self._model_lock:
            ask = self._model_ask
            if ask is not None and ask.get("status") == PENDING:
                if ask.get("trouble") == trouble:
                    self._looked_again(ask, exc)
                else:
                    self._settle_model(ask, REPLACED, "the model failed another way")
                    ask = None
            if ask is None or ask.get("status") != PENDING:
                ask = self._new_model_ask(trouble, exc)
            kick = self._model_kick
        self._waiting_begins()
        try:
            while ask.get("status") == PENDING:
                if self.stop_now or self.stopping:
                    self._settle_model(ask, UNANSWERED, "the run was stopped while it waited")
                    break
                if self._model_kick != kick:
                    # "Try again now" is a real try: what was last heard of the
                    # model is forgotten first - kept, it answered the try
                    # with the failure it remembered, not the model's own.
                    invalidate = getattr(self.owner.models, "invalidate", None)
                    if callable(invalidate):
                        invalidate()
                    return
                due = float(ask.get("next_at") or 0)
                if due and not self.pause_asked and time.time() >= due:
                    return
                time.sleep(WAIT_STEP)
        finally:
            self._waiting_ends()
        if ask.get("status") in (CONTINUED, REPLACED):
            return
        if ask.get("status") == UNANSWERED:
            raise ModelShut(self._stopped_waiting())
        self._model_shut = self._model_shut or str(ask.get("shut") or "you ended the run")
        raise ModelShut(self._model_shut)

    def _new_model_ask(self, trouble: str, exc: BaseException) -> Dict[str, Any]:
        """What the run now waits for, kept in its plan as a request of the
        user's: what failed, why, where to mend it, when it looks again."""
        availability = getattr(exc, "availability", None)
        resolved = getattr(exc, "resolved", None)
        provider = str(getattr(resolved, "provider_id", "") or "")
        reason = _clip(str(getattr(availability, "reason", "") or exc), 400)
        ask = {"id": f"model_{uuid.uuid4().hex[:10]}", "kind": "model", "trouble": trouble,
               "status": PENDING, "asked_at": _now(), "provider": provider,
               "provider_name": self._provider_name(provider),
               "model": str(getattr(resolved, "label", "") or ""),
               "state": str(getattr(getattr(availability, "state", None), "value", "") or ""),
               "reason": reason, "looks": 0}
        self._schedule_look(ask, f"{reason} {exc}")
        book = self.plan.setdefault("model_waits", [])
        book.append(ask)
        del book[:-50]
        self._model_ask = ask
        with self._answer_lock:
            self._answers[ask["id"]] = threading.Event()
        self.status = WAITING
        self.activity = "waiting for you: " + _clip(
            ("the model's usage limit" if trouble == MODEL_LIMIT else "the model") + f" - {reason}",
            100)
        self._note("model limit reached" if trouble == MODEL_LIMIT
                   else "model not answering" if trouble == MODEL_STUCK else "model needs you",
                   request=ask["id"], reason=reason, resets_at=ask.get("resets_at", ""),
                   next_look=ask.get("next_look", ""), provider=ask["provider_name"])
        return ask

    def _looked_again(self, ask: Dict[str, Any], exc: BaseException) -> None:
        """Looked again, and it still fails the same way: when next."""
        availability = getattr(exc, "availability", None)
        reason = _clip(str(getattr(availability, "reason", "") or exc), 400)
        ask.update(looks=int(ask.get("looks") or 0) + 1, looked_at=_now(), reason=reason)
        self._schedule_look(ask, f"{reason} {exc}")
        resets = f" - it resets at {_clock(ask['resets_ts'])}" if ask.get("resets_ts") else ""
        nxt = (f"next look at {_clock(float(ask['next_at']))}" if ask.get("next_at")
               else "waiting for you")
        self._event("run", "retry", f"Looked at the model again: still {reason[:160]}{resets}; "
                    f"{nxt}", request=ask["id"])
        self.owner.save(self)

    def _schedule_look(self, ask: Dict[str, Any], said: str) -> None:
        now = time.time()
        resets = limit_resets_at(said, now) if ask["trouble"] == MODEL_LIMIT else None
        if ask["trouble"] == MODEL_STUCK:
            ask.update(resets_ts=0, resets_at="", next_at=0, next_look="")
            return
        if ask["trouble"] == MODEL_LIMIT:
            at = resets + LIMIT_RESET_GRACE if resets else now + LIMIT_LOOK_SECONDS
        else:
            at = now + YOURS_LOOK_SECONDS[min(int(ask.get("looks") or 0),
                                              len(YOURS_LOOK_SECONDS) - 1)]
        at = max(at, now + LOOK_AT_LEAST)
        ask.update(resets_ts=resets or 0, resets_at=_iso_at(resets) if resets else "",
                   next_at=at, next_look=_iso_at(at))

    def _settle_model(self, ask: Dict[str, Any], status: str, note: str) -> None:
        """A wait on the model ends - once."""
        with self._model_lock:
            if ask.get("status") != PENDING:
                return
            ask.update(status=status, answered_at=_now(), note=note)
            if status == ENDED:
                ask["shut"] = f"you ended the run while the model needed you ({ask.get('reason', '')})"
            with self._answer_lock:
                event = self._answers.pop(ask["id"], None)
                waiting = bool(self._answers)
            if event is not None:
                event.set()
            if not waiting:
                self.status = PAUSED if self.pause_asked else "running"
                self.activity = ""
        self._note("model wait " + status, request=ask["id"], note=note)

    def _provider_name(self, provider: str) -> str:
        try:
            found = self.owner.models.provider(provider) if provider else None
        except Exception:        # pragma: no cover - named by its id
            found = None
        return str(getattr(found, "display_name", "") or provider or "the model")

    def _call_once(self, who: str, request: ModelRequest) -> Any:
        """ONE MODEL CALL: made, its tokens counted - what went in and what
        came out, apart - and recorded whole in the run's transcript, a call
        that failed too. Never a reason for the work to fail: a transcript
        that cannot be written is noted and the work goes on."""
        from ...heartbeat.experience.store import count_use
        response, error = None, ""
        # When it began - before it was made - so the transcript says when it
        # started and finished, and how long it took.
        started = time.time()
        try:
            response = self.owner.models.invoke(request, role="primary")
            return response
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._inflight.pop(threading.get_ident(), None)
            usage = getattr(response, "usage", None)
            part = self._part or self.stage_now or self.plan.get("phase", "")
            read = input_of(usage)
            tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
            with self._books:
                if response is not None:
                    count_use(self.used, response)
                self._record_call(who, part, started, read, tokens_out, error)
            # Its answer, for this thread's caller: a slide spec that does not
            # read as JSON is sent back to be corrected (`_drawn_slides`).
            self._local_state().answer = str(getattr(response, "text", "") or "")
            try:
                self.transcript.record(
                    who, part, getattr(response, "model", "") or "",
                    read["tokens_in"], tokens_out,
                    request.system or "", request.messages, response,
                    tools=request.tools, error=error, started=started,
                    cached=read["tokens_in_cached"],
                    cache_written=read["tokens_in_cache_written"])
            except Exception:        # pragma: no cover - the work goes on
                log.warning("could not write the transcript of %s", self.id, exc_info=True)

    def _local_state(self) -> threading.local:
        local = self.__dict__.get("_thread_state")
        if local is None:
            local = self.__dict__.setdefault("_thread_state", threading.local())
        return local

    @property
    def _part(self) -> str:
        return getattr(self._local_state(), "part", "")

    @_part.setter
    def _part(self, value: str) -> None:
        self._local_state().part = value

    def _active_before(self) -> float:
        """The run's own time in its sessions before this one - carried in
        its plan, set when this session began."""
        if not hasattr(self, "_active_carried"):
            self._active_carried = float(self.plan.get("active_seconds_ended") or
                                         self.plan.get("active_seconds") or 0.0)
        return self._active_carried

    def _record_call(self, who: str, part: str, started: float,
                     read: Dict[str, int], tokens_out: int, error: str) -> None:
        """ONE MODEL CALL, kept for the chart of the run's calls over time:
        when it began and ended, who made it - the builder, the reviewer,
        or the single model - in which part of the run, what went in (all
        of it, and of that how much was read from the cache and how much
        written to it) and what came out; and whether it failed. The last
        `CALLS_KEPT` are kept, across its sessions, each numbered in order."""
        try:
            finished = time.time()
            named = CALL_WHO.get(who, str(who or ""))
            if self.variant == SINGLE and named == "builder":
                named = "single"
            number = int(self.plan.get("calls_made") or 0) + 1
            # THE RUN'S OWN TIME when it ended: this session's - not paused,
            # not waiting for the user - on top of the sessions before it.
            active = self._active_before() + self._minutes() * 60
            self.plan["active_seconds"] = round(active, 1)
            entry = {"n": number,
                     "started": datetime.fromtimestamp(started, timezone.utc).isoformat(),
                     "finished": datetime.fromtimestamp(finished, timezone.utc).isoformat(),
                     "seconds": round(max(0.0, finished - started), 2),
                     "active": round(active, 1),
                     "who": named, "part": str(part or "")[:120],
                     "tokens_in": int(read.get("tokens_in") or 0),
                     "tokens_out": int(tokens_out or 0)}
            if read.get("tokens_in_cached") or read.get("tokens_in_cache_written"):
                entry["tokens_in_cached"] = int(read.get("tokens_in_cached") or 0)
                entry["tokens_in_cache_written"] = int(read.get("tokens_in_cache_written") or 0)
            if error:
                entry["failed"] = True
                entry["error"] = str(error)[:300]
            calls = self.plan.setdefault("calls", [])
            calls.append(entry)
            del calls[:-CALLS_KEPT]
            self.plan["calls_made"] = number
        except Exception:        # pragma: no cover - a record, not the work
            log.debug("could not keep a model call of %s", self.id, exc_info=True)

    def _system(self) -> str:
        where = [str(self.folder), *[str(one) for one in
                                     self.runtime.policy.extra_writable]]
        packages = ("The user approves each request before anything is "
                    "installed, so ask once for everything you need, together, "
                    "and say why." if self.packages_mode == ASK else
                    "They are installed without asking the user, and each install "
                    "is recorded for them: ask only for what the work needs.")
        system = ALONE_SYSTEM if self.variant == SINGLE else AGENT_SYSTEM
        environment = environment_rules("; ".join(where),
                                        network_rules(browser=self._has_browser()),
                                        debug=bool(self.debug))
        return system.replace("{environment}", environment).replace(
            "{packages}", packages) \
            + "\n\n" + zone_rule()

    def _has_browser(self) -> bool:
        """Whether this run's code may start the testing browser."""
        try:
            return getattr(self.runtime, "browser", lambda: None)() is not None
        except Exception:
            return False

    def _tree(self) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
        if self.variant == SINGLE:
            # The single model keeps no tree: nothing is made for it, and
            # what asks for one is told of a root with nothing under it.
            return {"id": "", "title": self.unit.get("name", ""), "statement": "",
                    "status": "", "parent_id": ""}, {}
        return self.writer.tree(self.work_unit)

    def _open(self, cards: Dict[str, Dict[str, Any]], root: Dict[str, Any]
              ) -> List[Dict[str, Any]]:
        return [card for card in cards.values() if card["id"] != root["id"]
                and card["status"] in ("open", "working")]

    def _contract_text(self, for_reviewer: bool = False) -> str:
        """The contract, and what each open card must meet. For the work
        agent: plain requirements. For the reviewer: with what each rests on."""
        contract = self.plan.get("contract") or {}
        if not contract:
            return ""
        root, cards = self._tree()
        lines = ["THE CONTRACT (approved - the goal is locked"
                 + ("; amended for this branch by the user's own words, below"
                    if contract.get("instructions") else "") + "):",
                 f"GOAL: {contract.get('goal', '')}"]
        if contract.get("scope"):
            lines.append("IN SCOPE: " + "; ".join(contract["scope"]))
        if contract.get("out_of_scope"):
            lines.append("OUT OF SCOPE: " + "; ".join(contract["out_of_scope"]))
        words = self._words_text()
        if words:
            lines.append(words)
        lines += self._promises_text()
        tiered = bool(self.plan.get("tiers"))
        lines.append("WHAT EACH OPEN CARD MUST MEET:")
        for card in self._open(cards, root)[:14]:
            checked = card.get("checked_by") or {}
            tier = f", {self._tier_of(card, cards)}" if tiered else ""
            lines.append(f"  [{card['id']}] {card['title']} ({card['status']}{tier}) - "
                         f"{card['statement'][:300]}")
            for one in checked.get("criteria") or []:
                if for_reviewer:
                    lines.append(f"    - {one['text']} (backed by {one['backed_by']}"
                                 f"{'; ' + ', '.join(one['because']) if one.get('because') else ''}"
                                 f"{'; serves ' + one['serves'] if one.get('serves') else ''})")
                else:
                    lines.append(f"    - {_plain(one['text'])}"
                                 + (" (polish: after the core promises hold)"
                                    if one.get("serves") == "polish" else ""))
            if checked.get("approach"):
                approach = checked["approach"] if for_reviewer else _plain(checked["approach"])
                lines.append(f"    how to go about it: {approach}")
            if not checked.get("criteria"):
                lines.append("    (criteria not yet written)")
        return "\n".join(lines)

    def _branch_words(self) -> List[str]:
        """The user's words for this line of the work, as they said them:
        kept in its contract once amended, or held for the contract still
        to be drafted."""
        contract = self.plan.get("contract") or {}
        said = contract.get("instructions") if contract else self.plan.get("pending_words")
        return [str(one) for one in said or []]

    def _words_text(self) -> str:
        """The user's words for this line, VERBATIM - read by both agents
        for the rest of it, the work agent's and the reviewer's alike."""
        words = self._branch_words()
        if not words:
            return ""
        return ("THE USER'S INSTRUCTION FOR THIS BRANCH (their own words, verbatim - "
                "where anything above says otherwise, these stand):\n"
                + "\n".join(f"  <<<\n{one}\n  >>>" for one in words))

    def _brief(self) -> str:
        """What the work agent is told of the project: the work, what was
        asked, where it may write, the project's folders, its record, the
        contract, and this work's tree - each bounded. None of the user's
        experience."""
        workspace = ""
        if self.owner.experience is not None and not self.debug:
            held = self.owner.experience.store.workspace(self.unit["id"]) or {}
            workspace = "; ".join(held.get("paths") or ([held["path"]]
                                                       if held.get("path") else []))
        record = "(no record of it yet)"
        if self.live and self.live_writer is not None and \
                self.live_writer.store.root(self.live["id"]) is not None:
            record = self.live_writer._frontier(self.live)[0]
        mine = self.writer._frontier(self.work_unit)[0]
        described = self._description()
        why = str(self.live.get("why") or "") if self.live else ""
        return "\n".join(filter(None, [
            today_line(),
            f"THE WORK: {self.unit.get('name', '')}",
            self._description_text(),
            # What reading its folders says it is for - not again when it is
            # only the user's own description.
            why if why.strip() != described.strip() else "",
            f"\nWHAT THE USER ASKED: "
            f"{self.instruction or 'carry the work forward towards its goal'}",
            f"\nTHE PROJECT'S FOLDERS (read them): {workspace}" if workspace else "",
            "\nYOU MAY WRITE IN: " + "; ".join(
                [str(self.folder), *[str(one) for one in
                                     self.runtime.policy.extra_writable]]),
            f"\nTHE PROJECT'S RECORD SO FAR (the user's own work on it):\n{record}",
            "\n" + self._contract_text() if self.plan.get("contract") else "",
            # No contract yet: the words go into the one drafted.
            "\n" + self._words_text() if not self.plan.get("contract")
            and self._branch_words() else "",
            f"\nTHIS WORK'S TREE (every card, the latest attempts):\n{mine}"]))

    def _description(self) -> str:
        """The user's own description of the project, if they wrote one -
        kept apart from what reading its folders says it is for. A project
        of the Experience debugger has its own, given when it was made."""
        if self.debug:
            return str(self.unit.get("description") or "").strip()
        if self.owner.experience is None:
            return ""
        try:
            return description_of(self.owner.experience.store.workspace(self.unit["id"]))
        except Exception:        # pragma: no cover - the work goes on without it
            log.debug("could not read the description of %s", self.unit.get("id"),
                      exc_info=True)
            return ""

    def _description_text(self) -> str:
        """THE USER'S DESCRIPTION, VERBATIM - for both agents, in every
        context: drafting the contract, amending it, each round, each
        review, the slides."""
        described = self._description()
        return f"The user's description of this project: {described}" if described else ""

    def _last_findings(self) -> str:
        """What the reviewer said of the last round - its words, without
        what they rest on."""
        findings = (self.plan.get("findings") or {}).get("findings") or []
        if not findings:
            return ""
        return "\n".join(f"  - ({one.get('type', '')}) {_plain(one.get('say', ''))}"
                         for one in findings[:12])

    # -- the loop ------------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            if self.variant == SINGLE:
                # One model on its own - and then the same end as any run:
                # the session's use, and the version it ends with.
                self._wrap_up_alone(self._alone())
                return
            if not self.resumed:
                root = self.writer.root_of(self.work_unit)
                if self.instruction:
                    self._record_instruction(root)
            else:
                self._note("continued", phase=self.plan.get("phase"))
                self._slides_left_behind()
            ended = ""
            if self.amend_words:
                self._note("branch instruction", text=self.amend_words)
                if self.plan.get("contract"):
                    if not self._amend():
                        ended = "not amended"
                else:
                    # No contract yet to amend: the words go into the one
                    # drafted, kept in it as they were said.
                    self.plan["pending_words"] = [*self._branch_words(), self.amend_words]
            if not ended and self.plan.get("phase") == "contract":
                if not self._contract():
                    ended = "paused" if self.stop_reason else "no contract"
            if not ended:
                ended = self._work()
            self._wrap_up(ended)
        except ModelShut as exc:
            # Not a failure of the work: it can be continued once the model
            # answers again.
            self.status = NOT_FINISHED
            self.stop_reason = self.stop_reason or "the model needs you"
            self._note("ended", status=self.status, reason=f"{self.stop_reason} - {exc}")
        except Exception as exc:          # pragma: no cover - reported
            log.exception("a work run failed")
            self.status = "failed"
            self._note("failed", error=str(exc)[:800])
        finally:
            self.ended_at = _now()
            self.activity = ""
            session = self.plan["sessions"][-1]
            # The same count the session goes on adding to: the slides of
            # the version it ends with are a model call too.
            session.update(ended=self.ended_at, used=self.used,
                           ended_because=self.stop_reason or self.status,
                           minutes=round(self._minutes(), 1),
                           paused_minutes=round(self._paused_seconds() / 60, 1))
            total = self.plan.setdefault("total", {})
            for name in ("calls", "tokens_in", "tokens_out"):
                total[name] = sum(int((one.get("used") or {}).get(name, 0))
                                  for one in self.plan["sessions"])
            self.owner.save(self)
            # ITS JOBS end with it: nothing a run started outlives its session.
            try:
                self.tools.stop_jobs()
            except Exception:        # pragma: no cover - each ends at its time
                log.debug("could not stop the jobs of %s", self.id, exc_info=True)
            # KEY POINT: the session ended - finished, paused, stopped.
            self._end_version()
            # ITS VERSIONS' SLIDES, drawn behind the work: the session ends
            # once they are, so what they cost is counted in it.
            waiting = self._behind.waiting()
            if waiting:
                self.activity = "drawing the slides of " + ", ".join(waiting)
            self._behind.wait()
            self.activity = ""
            for name in ("calls", "tokens_in", "tokens_out"):
                total[name] = sum(int((one.get("used") or {}).get(name, 0))
                                  for one in self.plan["sessions"])
            self.owner.save(self)
            # THE END OF THE SESSION, as the user is told of it when they
            # asked to be: a sound - once, now, never for a call or a round.
            self._ring()

    @property
    def live(self) -> Optional[Dict[str, Any]]:
        """THE PROJECT AS THE RECORD HAS IT NOW - read again when it was read
        more than `LIVE_FRESH_SECONDS` ago. Learning going on beside the run
        adds tasks and stages to it as it goes: what is learned after the run
        began is found by the run's searches of its project, and its cards
        are opened, as soon as it is written."""
        unit = self.__dict__.get("_live_unit")
        if unit is None:
            return None
        if time.time() - self._live_at > LIVE_FRESH_SECONDS:
            self._live_at = time.time()
            try:
                fresh = self.owner.experience.full_unit(unit["id"])
            except Exception:        # pragma: no cover - the one read before
                log.debug("could not read %s again", unit.get("id"), exc_info=True)
                fresh = None
            if fresh is not None:
                self._live_unit = unit = fresh
        return unit

    @property
    def project_tasks(self) -> List[str]:
        """The tasks the project's stages are kept under - its tasks now."""
        from ...heartbeat.experience.stages import unit_task_ids
        live = self.live
        return unit_task_ids(live) if live else []

    def _ring(self) -> None:
        """The sound this session ends with, if the user asked for one: of
        how it ended - finished, failed, or stopped otherwise (a limit, the
        user, a stall). Played by the application, once; a player that
        fails is noted, never the run's failure."""
        if not self.sound or self._rung:
            return
        self._rung = True
        outcome = (SOUND_DONE if self.status == DONE
                   else SOUND_FAILED if self.status == "failed" else SOUND_STOPPED)
        try:
            self.owner.play_sound(outcome)
        except Exception:        # pragma: no cover - a sound, not the work
            log.debug("could not play the sound %s ended with", self.id, exc_info=True)

    # -- one model on its own: the Experience debugger's first way -------------------------------
    def _alone_brief(self) -> str:
        """What the single model is given: the task as the user gave it -
        its name, their description and their instruction, the same words
        the other two ways start from - their words since, where it may
        write, and, continued, what it reported last."""
        plan = self.plan
        words = [str(one) for one in plan.get("words") or []]
        parts = [f"THE TASK: {self.unit.get('name', '')}",
                 self._description_text(),
                 f"\nWHAT THE USER ASKED: {self.instruction or 'carry the work forward'}"]
        if words:
            parts.append("\nTHE USER'S WORDS SINCE (verbatim - where anything above "
                         "says otherwise, these stand):\n"
                         + "\n".join(f"  <<<\n{one}\n  >>>" for one in words))
        parts.append("\nYOU MAY WRITE IN: " + str(self.folder))
        if self.resumed and plan.get("last_report"):
            parts.append("\n" + ALONE_RESUMED.format(
                folder=str(self.folder), last=str(plan.get("last_report"))[:12000],
                next=f"WHAT YOU SAID YOU WOULD DO NEXT: {plan['next']}"
                if plan.get("next") else ""))
        return "\n".join(filter(None, parts))

    def _alone(self) -> str:
        """THE SINGLE MODEL AT WORK: the task, given once, and the model's
        own loop - as many tools as it wants in a turn, then a report of
        where it is; told to go on until it says it is done. No contract,
        no cards, no stages, no reviewer. It stops when it says the whole
        task is done, at a limit (if one was set - by default none is), when
        the user stops it, or after STALLS_TO_STOP turns in a row with no
        tool used and no end said. A version is saved after each turn, so
        it can be rolled back and continued like any run."""
        self.plan["phase"] = "alone"
        if self.resumed:
            self._note("continued", phase="work")
        else:
            self._note("instruction", text=self.instruction[:500])
        if self.amend_words:
            # No contract to amend: the words join the task, as said.
            self._note("branch instruction", text=self.amend_words)
            self.plan["words"] = [*[str(one) for one in self.plan.get("words") or []],
                                  self.amend_words]
        self._stage("alone", "One model on its own: no contract, no reviewer, no "
                    "stages - it works until it says it is done")
        messages: List[Dict[str, Any]] = [{"role": "user", "content": self._alone_brief()}]
        quiet = 0
        while True:
            # SAFE POINT: between turns.
            self._hold("between turns")
            if not self._going():
                return "paused"
            number = int(self.plan.get("round") or 0) + 1
            self._stage(f"turn {number}", f"Turn {number}")
            tools_before = self._worker_tools
            said = self._ask(self._system(), messages, self.tools, writing=True,
                             rounds=WORK_TOOL_ROUNDS, pause=ALONE_PAUSE)
            if not said:
                return "paused"
            answer = messages[-1].get("content") if messages[-1].get("role") == "assistant" \
                else ""
            report = str(said.get("report") or "").strip() or str(answer or "").strip()
            used = self._worker_tools - tools_before
            interrupted = bool(said.get("interrupted"))
            done = said.get("done") is True and not interrupted
            progress = ("done" if done else "interrupted" if interrupted
                        else "went on" if used else "no tool used")
            self.plan["round"] = number
            self.plan.setdefault("rounds", []).append({
                "n": number, "card": f"Turn {number}", "objective": _clip(report, 300),
                "progress": progress, "tools": used, "at": _now()})
            self.plan["last_report"] = report[:20000]
            self.plan["next"] = str(said.get("next") or "")[:2000]
            self._event("worker", "milestone", f"Turn {number}: {progress} - "
                        f"{_clip(report, 200)}", full=report)
            self.owner.save(self)
            if interrupted:
                return "paused"
            if done:
                return "complete"
            quiet = 0 if used else quiet + 1
            if quiet >= STALLS_TO_STOP:
                self.stop_reason = (f"{STALLS_TO_STOP} turns in a row without a tool "
                                    "used or the work said to be done")
                return "stalled"
            # KEY POINT: a turn ended, and the work goes on.
            self._version("turn", f"Turn {number}: {progress}",
                          message=commit_message(said, re.split(r"(?<=[.!?。])\s", report)[0]
                                                 or f"Turn {number}"),
                          round=number, progress=progress)
            if len(json.dumps(messages, ensure_ascii=False, default=str)) > ALONE_CONTEXT_CHARS:
                reports = "\n".join(f"  {one['n']}. {one['objective']}"
                                    for one in self.plan["rounds"][-40:])
                messages[:] = [{"role": "user", "content": self._alone_brief() + "\n\n"
                                + ALONE_RESTARTED.format(reports=reports)
                                + "\n\n" + ALONE_GO_ON}]
            else:
                messages.append({"role": "user", "content": ALONE_GO_ON})

    def _wrap_up_alone(self, ended: str) -> None:
        """The single model's end: how it ended, what is next if it is not
        done, and its report - the run's final word, as it wrote it."""
        finished = ended == "complete"
        self.status = {"complete": DONE, "paused": NOT_FINISHED,
                       "stalled": STALLED}.get(ended, NOT_FINISHED)
        if finished:
            self.plan["phase"] = "done"
        else:
            self.plan["todo"] = {"open": [], "blocked": [], "interrupted": {},
                                 "next": [one for one in [self.plan.get("next")] if one],
                                 "final_review": []}
        report = str(self.plan.get("last_report") or "")
        # The README of what was made, when anything was.
        if finished or any(Path(one).exists() for one in self.plan.get("changed_files") or []):
            self._readme(finished, alone=True)
        total = {k: int((self.plan.get("total") or {}).get(k, 0)) + int(self.used.get(k, 0))
                 for k in ("calls", "tokens_in", "tokens_out")}
        self.final = dict(self.final, done=finished, ended=ended, reason=self.stop_reason,
                          answer=report, rounds=len(self.plan.get("rounds") or []),
                          total=total, report=f"Run {self.id}: {self.status}")
        self._write(f"runs/{self.id}/report.md", "\n".join([
            f"# Report - {self.unit.get('name', '')}", "",
            f"Run {self.id}: {self.status}"
            + (f" ({self.stop_reason})" if self.stop_reason else ""), "",
            f"Asked: {self.instruction}", "",
            f"This session: {self.used['calls']} model calls, "
            f"{self.used['tokens_in'] + self.used['tokens_out']} tokens "
            f"({self.used['tokens_in']} in, {self.used['tokens_out']} out), "
            f"{round(self._minutes(), 1)} minutes", "",
            "## Its report", "", report or "(none)"]))
        self._note("ended", status=self.status, reason=self.stop_reason)

    def _record_instruction(self, root: Dict[str, Any]) -> None:
        """What the user asked, kept as the goal they set: a stage of the
        work, decided by them."""
        stamp = _now()
        self.store.add_stage({
            "task_id": self.unit["id"], "card_id": root["id"], "kind": "goal",
            "medium": "", "summary": f"The user asked: {self.instruction}"[:2000],
            "states": [], "began": stamp, "ended": stamp,
            "work": {"input": [{"what": self.instruction, "from": "user"}],
                     "decision": {"about": "what this run is for",
                                  "chose": self.instruction, "by": "user",
                                  "why": "the user's starting instruction"},
                     "output": {"what": self.instruction}},
            "notes": {"run": self.id, "instruction": True},
            "written_by": {"model": "", "provider": "", "at": stamp}})
        self._note("instruction", text=self.instruction[:500])

    # -- A. the contract ------------------------------------------------------------------------
    def _contract(self) -> bool:
        """Drafted by the work agent, criteria and approach written and the
        whole approved by the reviewer, then locked."""
        self._stage("contract", "The contract: the work agent drafts the goal, the scope "
                    "and the deliverables; the reviewer writes what each must meet")
        messages = [{"role": "user", "content": self._brief() + "\n\n" + DRAFT_CONTRACT}]
        draft = self._ask(self._system(), messages, self.tools, writing=False)
        if not self._going():
            return False
        review = Review(self, self._contract_profile(), self._reviewer_context(None))
        reviews: List[Dict[str, Any]] = []
        judged: Dict[str, Any] = {}
        for number in range(CONTRACT_REVIEWS):
            last = number == CONTRACT_REVIEWS - 1
            judged = review.ask(REVIEW_CONTRACT.format(
                contract=json.dumps({k: v for k, v in draft.items() if k != "written_by"},
                                    ensure_ascii=False)[:12000],
                criteria=CRITERIA_SHAPE, note=CRITERIA_NOTE, for_user=FOR_USER_NOTE,
                last=("\nThis is the LAST review: approve it, or it is not "
                      "approved and no work is done." if last else "")))
            reviews.append(dict(judged, at=_now(), on="the contract", round=number + 1))
            verdict = str(judged.get("verdict") or "").lower()
            self._note("contract reviewed", verdict=verdict,
                       comments=[c.get("say") for c in judged.get("comments") or []][:6])
            if verdict.startswith("approve") and draft.get("goal"):
                break
            if last or not self._going():
                self._record_contract(draft, reviews, approved=False)
                return False
            messages.append({"role": "user", "content": REVISE_CONTRACT.format(
                review=json.dumps({k: judged.get(k) for k in ("comments", "change")},
                                  ensure_ascii=False)[:6000])})
            draft = self._ask(self._system(), messages, self.tools, writing=False)
            if not self._going():
                return False
        self._apply_contract(draft, judged)
        self._for_user(judged, "the contract")
        self._record_contract(draft, reviews, approved=True)
        self.plan["phase"] = "rounds"
        self.owner.save(self)
        # KEY POINT: the contract approved.
        self._version("contract", "The contract approved", message=commit_message(
            None, f"Contract: {draft.get('goal') or 'approved'}",
            [f"deliverable: {one.get('title')}" for one in draft.get("deliverables") or []
             if isinstance(one, dict) and one.get("title")]))
        return True

    def _apply_contract(self, draft: Dict[str, Any], judged: Dict[str, Any]) -> None:
        root, cards = self._tree()
        goal = str(draft.get("goal") or "").strip()[:1500]
        self.store.update_card(root["id"], {"statement": goal, "status": "working"})
        entries = [dict(one, parent="root") for one in draft.get("deliverables") or []
                   if isinstance(one, dict)]
        refs = self.writer._apply_cards(self.work_unit, {"cards": entries}, root, cards,
                                        draft.get("written_by") or {})
        promises = promises_of(draft.get("core_promises"))
        self._set_tiers(refs, entries, promises)
        by_ref = {str(one.get("ref") or ""): one for one in judged.get("deliverables") or []
                  if isinstance(one, dict)}
        listed = []
        for ref, card in refs.items():
            said = by_ref.get(ref) or {}
            criteria = self._criteria(said.get("criteria"))
            if criteria:
                self._set_criteria(card, criteria, str(said.get("approach") or ""))
            listed.append({"card": card["id"], "title": card["title"],
                           "statement": card["statement"]})
        contract = {"goal": goal,
                    "scope": [str(one) for one in draft.get("scope") or []][:12],
                    "out_of_scope": [str(one) for one in draft.get("out_of_scope") or []][:12],
                    **({"promises": promises} if promises else {}),
                    "deliverables": listed, "approved_at": _now()}
        # Words the user continued with before there was a contract: in it,
        # as they said them - and on the version it is saved as.
        words = [str(one) for one in self.plan.pop("pending_words", None) or []]
        if words:
            contract["instructions"] = words
            self._amended = {"words": words, "said": words[-1],
                             "diff": contract_diff({}, contract), "reopened": []}
        self.plan["contract"] = contract
        self.runtime.write_text(str(self.contract_path.relative_to(self.folder)),
                                json.dumps(contract, ensure_ascii=False, indent=2))

    # -- the core promises and the tiers ------------------------------------------------
    def _set_tiers(self, refs: Dict[str, Dict[str, Any]], entries: List[Dict[str, Any]],
                   promises: List[Dict[str, Any]]) -> None:
        """Each card the draft named: its tier, and the promises it serves."""
        known = {one["id"] for one in promises} or {
            one["id"] for one in (self.plan.get("contract") or {}).get("promises") or []}
        tiers = self.plan.setdefault("tiers", {})
        for entry in entries:
            card = refs.get(str(entry.get("ref") or ""))
            if card is None:
                continue
            serves = [str(one) for one in entry.get("promises") or [] if str(one) in known]
            if entry.get("tier") or serves or card["id"] not in tiers:
                tiers[card["id"]] = {"tier": tier_of(entry.get("tier")), "promises": serves}

    def _tier_of(self, card: Optional[Dict[str, Any]], cards: Dict[str, Any]) -> str:
        """A card's tier: its own, or the nearest one above it that has one."""
        tiers = self.plan.get("tiers") or {}
        while card is not None:
            if card["id"] in tiers:
                return tiers[card["id"]]["tier"]
            card = cards.get(card.get("parent_id") or "")
        return "supporting"

    def _promises(self) -> List[Dict[str, Any]]:
        return list((self.plan.get("contract") or {}).get("promises") or [])

    def _unmet(self) -> List[str]:
        """The core promises that did not hold - or were not tried - when the
        reviewer last looked. One only the user can check is theirs, on the
        list for them: it does not hold the work open."""
        found = self.plan.get("promise_status") or {}
        return [one["id"] for one in self._promises()
                if (found.get(one["id"]) or {}).get("holds") not in ("yes", "needs_user")]

    def _for_user(self, said: Dict[str, Any], milestone: str) -> None:
        """What a milestone found only the user can check or do - added to
        the run's list once, with the milestone it came from."""
        kept = self.plan.setdefault("for_user", [])
        known = {" ".join(str(one.get("do") or "").lower().split()) for one in kept}
        promises = {one["id"] for one in self._promises()}
        # A promise only the user can check first: it says what was tried.
        asked = [{"do": one.get("user_does") or f"check that {one.get('id')} holds",
                  "why": one.get("tried") or "it cannot be tried here",
                  "promise": one.get("id")}
                 for one in said.get("promises") or []
                 if isinstance(one, dict) and str(one.get("holds") or "").lower() == "needs_user"]
        asked += [one for one in said.get("for_user") or [] if isinstance(one, dict)]
        for one in asked:
            do = " ".join(str(one.get("do") or "").split())[:500]
            if not do or do.lower() in known:
                continue
            known.add(do.lower())
            promise = str(one.get("promise") or "").strip()
            item = {"do": do, "why": str(one.get("why") or "")[:500],
                    "promise": promise if promise in promises else "",
                    "milestone": milestone, "round": int(self.plan.get("round") or 0),
                    "at": _now()}
            kept.append(item)
            if not hasattr(self, "_for_user_new"):
                self._for_user_new = []
            self._for_user_new.append(item)
        del kept[:-60]

    def _judge_promises(self, judged: Dict[str, Any], number: int) -> None:
        """What the reviewer found of each core promise this round."""
        ids = {one["id"] for one in self._promises()}
        found = self.plan.setdefault("promise_status", {})
        for one in judged.get("promises") or []:
            if not isinstance(one, dict) or str(one.get("id") or "") not in ids:
                continue
            holds = str(one.get("holds") or "").strip().lower()
            found[str(one["id"])] = {
                "holds": holds if holds in ("yes", "no", "untried", "needs_user")
                else "untried",
                "tried": str(one.get("tried") or "")[:600],
                "failed": str(one.get("failed") or "")[:600],
                "user_does": str(one.get("user_does") or "")[:600], "round": number}

    def _promises_text(self) -> List[str]:
        promises = self._promises()
        if not promises:
            return []
        found = self.plan.get("promise_status") or {}
        said = {"yes": "HOLDS", "no": "DOES NOT HOLD", "untried": "not tried yet",
                "needs_user": "only the user can check it"}
        lines = ["CORE PROMISES - what the user must get; the work is not done until "
                 "each holds when tried for real:"]
        for one in promises:
            state = found.get(one["id"]) or {}
            lines.append(f"  {one['id']} [{said.get(state.get('holds'), 'not tried yet')}"
                         f"{', round ' + str(state['round']) if state.get('round') else ''}] "
                         f"{one['promise']}")
            if one.get("real_condition"):
                lines.append(f"    tried by: {one['real_condition']}")
            if one.get("fails_if"):
                lines.append("    must survive: " + "; ".join(one["fails_if"]))
            if state.get("holds") == "no" and state.get("failed"):
                lines.append(f"    last failed: {state['failed']}")
        return lines

    def _criteria(self, said: Any) -> List[Dict[str, Any]]:
        """The criteria a reviewer wrote. One with no context has no
        experience to back one with: any it says is, is its own judgement."""
        criteria = _criteria_of(said)
        if self.variant == NO_CONTEXT:
            for one in criteria:
                if one["backed_by"] == "experience":
                    one["backed_by"] = "own judgement"
        return criteria

    def _set_criteria(self, card: Dict[str, Any], criteria: List[Dict[str, Any]],
                      approach: str) -> None:
        self.store.update_card(card["id"], {"checked_by": {
            "by": "agent", "what": "the reviewer, against its criteria",
            "criteria": criteria, "approach": approach[:1500]}})

    def _record_contract(self, draft: Dict[str, Any], reviews: List[Dict[str, Any]],
                         approved: bool) -> None:
        root, _cards = self._tree()
        stamp = _now()
        self.store.add_stage({
            "task_id": self.unit["id"], "card_id": root["id"], "kind": "plan",
            "medium": "", "summary": ("The contract, approved: " if approved else
                                      "The contract, not approved: ")
            + str(draft.get("goal") or "")[:1800],
            "states": [], "began": stamp, "ended": stamp,
            "work": {"decision": {"about": "what the work is to deliver",
                                  "chose": str(draft.get("goal") or ""), "by": "agent",
                                  "why": "the contract, approved by the reviewer"
                                  if approved else "the reviewer did not approve it"}},
            "evaluation": {"verdict": "approved" if approved else "not approved",
                           "criteria": [], "words": [c.get("say", "") for c in
                                                     (reviews[-1].get("comments") or [])
                                                     ][:10] if reviews else []},
            "notes": {"run": self.id, "contract": {k: v for k, v in draft.items()
                                                   if k != "written_by"},
                      "reviews": reviews},
            "written_by": dict(draft.get("written_by") or {}, at=stamp)})

    # -- A'. an amendment, by the user's words -------------------------------------------------
    def _contract_now(self) -> Dict[str, Any]:
        """The contract as it stands: as approved, with the deliverables the
        tree has now - those added by an approved amendment of a round too -
        and not those set aside."""
        contract = dict(self.plan.get("contract") or {})
        root, cards = self._tree()
        contract["deliverables"] = [
            {"card": card["id"], "title": card["title"], "statement": card["statement"]}
            for card in cards.values()
            if card.get("parent_id") == root["id"] and card["status"] != "set_aside"]
        return contract

    def _done_text(self, done: List[Dict[str, Any]]) -> str:
        if not done:
            return ""
        return ("\nWHAT IS DONE SO FAR - cards accepted under the contract before:\n"
                + "\n".join(f"  [{card['id']}] {card['title']} - {card['statement'][:300]}"
                            for card in done[:40]) + "\n")

    def _amended_contract(self, before: Dict[str, Any], draft: Dict[str, Any],
                          words: str) -> Dict[str, Any]:
        """The contract as the draft would make it - its deliverables by the
        refs the draft gave them - with the user's words added, as said."""
        known = {one["card"]: one for one in before.get("deliverables") or []}
        listed = []
        for one in draft.get("deliverables") or []:
            if not isinstance(one, dict):
                continue
            ref = str(one.get("ref") or "")
            listed.append({"card": ref,
                           "title": str(one.get("title") or (known.get(ref) or {}).get("title")
                                        or "")[:300],
                           "statement": str(one.get("statement") or "")[:1500]})
        promises = promises_of(draft.get("core_promises"))
        return {**{k: v for k, v in before.items() if k not in ("deliverables",)},
                **({"promises": promises} if promises else {}),
                "goal": str(draft.get("goal") or "").strip()[:1500],
                "scope": [str(one) for one in draft.get("scope") or []][:12],
                "out_of_scope": [str(one) for one in draft.get("out_of_scope") or []][:12],
                "deliverables": listed,
                "instructions": [*[str(one) for one in before.get("instructions") or []],
                                 words]}

    def _amend(self) -> bool:
        """THE CONTRACT AMENDED BY THE USER'S NEW WORDS, before any more work
        on this line: drafted by the work agent from the contract as it
        stands, the words verbatim and where the work is; checked by the
        reviewer - does it follow the words, all of them and only them, and
        is everything they do not touch as it was - approved or sent back, at
        most as often as the contract itself; the user is not asked. Two
        model calls when the first draft is approved (and the tools each
        uses). What changed is shown clause by clause; the done cards the
        reviewer names as no longer holding are opened again. Not approved,
        the contract stays as it was and the run does no work on it."""
        words = self.amend_words
        self._stage("amendment", "The contract amended for this branch: the work agent "
                    "drafts it from your words; the reviewer checks it")
        before = self._contract_now()
        root, cards = self._tree()
        done = [card for card in cards.values()
                if card["id"] != root["id"] and card["status"] == "done"]
        done_text = self._done_text(done)
        shown = {k: v for k, v in before.items()
                 if k in ("goal", "scope", "out_of_scope", "instructions")}
        shown["deliverables"] = [{"ref": one["card"], "title": one["title"],
                                  "type": (cards.get(one["card"]) or {}).get("type", ""),
                                  "statement": one["statement"]}
                                 for one in before["deliverables"]]
        messages = [{"role": "user", "content": self._brief() + "\n\n" + AMEND_CONTRACT.format(
            words=words, done=done_text,
            contract=json.dumps(shown, ensure_ascii=False, indent=1)[:12000])}]
        draft = self._ask(self._system(), messages, self.tools, writing=False)
        if not self._going():
            return False
        review = Review(self, self._contract_profile(words), self._reviewer_context(None))
        reviews: List[Dict[str, Any]] = []
        judged: Dict[str, Any] = {}
        approved = False
        for number in range(CONTRACT_REVIEWS):
            last = number == CONTRACT_REVIEWS - 1
            after = self._amended_contract(before, draft, words)
            judged = review.ask(REVIEW_AMENDMENT.format(
                words=words, done=done_text,
                before="\n".join(contract_lines(before)),
                after=json.dumps({k: v for k, v in draft.items() if k != "written_by"},
                                 ensure_ascii=False)[:12000],
                diff=contract_diff(before, after)[:12000],
                criteria=CRITERIA_SHAPE, note=CRITERIA_NOTE,
                last=("\nThis is the LAST review: approve it, or it is not approved "
                      "and the contract stays as it was." if last else "")))
            reviews.append(dict(judged, at=_now(), on="the amendment", round=number + 1))
            verdict = str(judged.get("verdict") or "").lower()
            self._note("amendment reviewed", verdict=verdict,
                       comments=[c.get("say") for c in judged.get("comments") or []
                                 if isinstance(c, dict)][:6],
                       reopen=[str(one.get("card") or "") for one in judged.get("reopen") or []
                               if isinstance(one, dict)][:20])
            if verdict.startswith("approve") and draft.get("goal"):
                approved = True
                break
            if last or not self._going():
                break
            messages.append({"role": "user", "content": REVISE_CONTRACT.format(
                review=json.dumps({k: judged.get(k) for k in ("comments", "change")},
                                  ensure_ascii=False)[:6000])})
            draft = self._ask(self._system(), messages, self.tools, writing=False)
            if not self._going():
                return False
        if not approved:
            self.stop_reason = self.stop_reason or "the amended contract was not approved"
            self._record_amendment(words, draft, reviews, "", [], approved=False)
            self._note("amendment not approved", words=words)
            return False
        after, reopened = self._apply_amendment(before, draft, judged, words, done)
        diff = contract_diff(before, after)
        self._record_amendment(words, draft, reviews, diff, reopened, approved=True)
        self._note("contract amended", words=words, diff=diff,
                   reopened=[one["title"] for one in reopened])
        # Kept on the version saved now - the first of this line that holds it.
        self._amended = {"words": list(after["instructions"]), "said": words, "diff": diff,
                         "reopened": [one["title"] for one in reopened]}
        if reopened:
            self.plan["resumed_from"] = dict(self.plan.get("resumed_from") or {}, reopened=[
                {"card": one["id"], "title": one["title"], "why": one["why"]}
                for one in reopened])
        # The work is judged again, against the amended contract.
        if self.plan.get("phase") in ("final", "presenting", "done"):
            self.plan["phase"] = "rounds"
        self.owner.save(self)
        # KEY POINT: the contract amended - named for what it added or
        # dropped, else for the user's words.
        had = {str(one.get("title") or "") for one in before.get("deliverables") or []}
        has = {str(one.get("title") or "") for one in after.get("deliverables") or []}
        added, dropped = sorted(has - had - {""}), sorted(had - has - {""})
        title = (f"Amend contract: add {', '.join(added)}" if added else
                 f"Amend contract: drop {', '.join(dropped)}" if dropped else
                 f"Amend contract: {words}")
        self._version("amended", "The contract amended for this branch",
                      message=commit_message(None, title, [
                          one.strip() for one in str(diff or "").splitlines()
                          if one.strip()][:6]))
        return True

    def _apply_amendment(self, before: Dict[str, Any], draft: Dict[str, Any],
                         judged: Dict[str, Any], words: str, done: List[Dict[str, Any]]
                         ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """The approved amendment, made so: the goal, each deliverable kept,
        changed, added or dropped (set aside, not deleted - the other line
        still has it), the criteria the reviewer wrote, the done cards it
        named opened again, and the contract file written. Returns the
        contract and the cards opened again."""
        root, cards = self._tree()
        goal = str(draft.get("goal") or "").strip()[:1500]
        fields: Dict[str, Any] = {}
        if goal != before.get("goal"):
            fields["statement"] = goal
        if root["status"] == "done":
            fields["status"] = "working"
        if fields:
            self.store.update_card(root["id"], fields)
        known = {one["card"] for one in before.get("deliverables") or []}
        kept: Dict[str, Dict[str, Any]] = {}
        added = []
        for one in draft.get("deliverables") or []:
            if not isinstance(one, dict):
                continue
            ref = str(one.get("ref") or "")
            card = cards.get(ref) if ref in known else None
            if card is None:
                added.append(dict(one, parent="root"))
                continue
            changed = {name: str(one.get(name))[:1500 if name == "statement" else 300]
                       for name in ("title", "statement")
                       if str(one.get(name) or "").strip() and str(one.get(name)) != card[name]}
            if changed:
                self.store.update_card(card["id"], changed)
            kept[ref] = card
        refs = self.writer._apply_cards(self.work_unit, {"cards": added}, root, cards,
                                        draft.get("written_by") or {}) if added else {}
        promises = promises_of(draft.get("core_promises")) or list(before.get("promises") or [])
        drafted = [one for one in draft.get("deliverables") or [] if isinstance(one, dict)]
        self._set_tiers({**kept, **refs}, drafted, promises)
        for ref in known - set(kept):
            self.store.update_card(ref, {"status": "set_aside", "status_why":
                                         "dropped by the contract amended for this branch"})
        # The criteria of what is new or changed, as the reviewer wrote them.
        by_ref = {**kept, **refs}
        for said in judged.get("deliverables") or []:
            if not isinstance(said, dict):
                continue
            card = by_ref.get(str(said.get("ref") or ""))
            criteria = self._criteria(said.get("criteria"))
            if card is not None and criteria:
                self._set_criteria(card, criteria, str(said.get("approach") or ""))
        # Done work that no longer holds, opened again - and what it is part
        # of, which cannot be done while it is not.
        done_by_id = {card["id"]: card for card in done}
        reopened = []
        for one in judged.get("reopen") or []:
            if not isinstance(one, dict):
                continue
            card = done_by_id.pop(str(one.get("card") or ""), None)
            if card is None or (card["id"] in known and card["id"] not in kept):
                continue            # not done, or dropped from this line
            why = str(one.get("why") or "")[:400]
            self.store.update_card(card["id"], {
                "status": "open", "status_why": f"opened again by the amended contract: {why}"})
            reopened.append({"id": card["id"], "title": card["title"], "why": why})
            # NOT AGAINST THE NATURES: it is the reviewer that opened it
            # again, and only what the user does themselves moves how sure
            # a nature is (heartbeat/lessons).
            parent = cards.get(card.get("parent_id") or "")
            while parent is not None and parent["id"] != root["id"]:
                if parent["status"] == "done":
                    self.store.update_card(parent["id"], {"status": "working"})
                parent = cards.get(parent.get("parent_id") or "")
        after = self._amended_contract(before, draft, words)
        after["deliverables"] = [
            dict(one, card=(by_ref.get(one["card"]) or {}).get("id", one["card"]))
            for one in after["deliverables"]]
        contract = dict(self.plan.get("contract") or {}, goal=after["goal"],
                        scope=after["scope"], out_of_scope=after["out_of_scope"],
                        **({"promises": promises} if promises else {}),
                        deliverables=after["deliverables"],
                        instructions=after["instructions"], amended_at=_now())
        self.plan["contract"] = contract
        self.runtime.write_text(str(self.contract_path.relative_to(self.folder)),
                                json.dumps(contract, ensure_ascii=False, indent=2))
        return contract, reopened

    def _record_amendment(self, words: str, draft: Dict[str, Any],
                          reviews: List[Dict[str, Any]], diff: str,
                          reopened: List[Dict[str, Any]], approved: bool) -> None:
        """The amendment, kept in the work's tree - so on this line only: the
        user's words as their decision, and what the contract became."""
        root, _cards = self._tree()
        stamp = _now()
        self.store.add_stage({
            "task_id": self.unit["id"], "card_id": root["id"], "kind": "plan",
            "medium": "", "summary": ("The contract amended for this branch, by the "
                                      "user's words: " if approved else
                                      "An amendment of the contract, not approved: ")
            + words[:1800],
            "states": [], "began": stamp, "ended": stamp,
            "work": {"input": [{"what": words, "from": "user"}],
                     "decision": {"about": "what this branch of the work is to deliver",
                                  "chose": words, "by": "user",
                                  "why": "the user's words when they continued the work"}},
            "evaluation": {"verdict": "approved" if approved else "not approved",
                           "criteria": [], "words": [c.get("say", "") for c in
                                                     (reviews[-1].get("comments") or [])
                                                     if isinstance(c, dict)][:10]
                           if reviews else []},
            "notes": {"run": self.id, "amendment": {
                "words": words, "diff": diff, "reopened": reopened,
                "contract": {k: v for k, v in draft.items() if k != "written_by"}},
                "reviews": reviews},
            "written_by": dict(draft.get("written_by") or {}, at=stamp)})

    # -- B. rounds --------------------------------------------------------------------------------
    def _work(self) -> str:
        """Rounds until the goal is met, then the final review and its fixes.
        Returns how the work ended: complete, paused, stalled, blocked or
        stop."""
        while True:
            phase = self.plan.get("phase")
            if phase in ("presenting", "done"):
                return "complete"
            # SAFE POINT: between rounds.
            self._hold("between rounds")
            if not self._going():
                return "paused"
            if phase == "rounds":
                outcome = self._round()
                if outcome == "complete":
                    self.plan["phase"] = "final"
                elif outcome != "next":
                    return outcome
            elif phase == "final":
                final = self._final()
                if final == "clean":
                    self.plan["phase"] = "presenting"
                    return "complete"
                if final == "unmet":
                    return "stalled"
                if not self._going():
                    return "paused"
                outcome = self._round(fix=(self.plan.get("final_review") or {}).get("to_fix"))
                if outcome not in ("next", "complete"):
                    return outcome
            else:
                return "paused"

    def _write_criteria(self) -> None:
        """Cards opened since the last round get their criteria - written by
        the reviewer, from the experience retrieved for each."""
        root, cards = self._tree()
        waiting = [card for card in self._open(cards, root)
                   if not (card.get("checked_by") or {}).get("criteria")]
        for card in waiting[:CRITERIA_PER_ROUND]:
            if not self._going():
                return
            review = Review(self, self._card_profile(card, {}), self._reviewer_context(card),
                            same_as=f"criteria:{card.get('id', '')}")
            said = review.ask(REVIEW_CARD.format(
                card=json.dumps({"id": card["id"], "title": card["title"],
                                 "statement": card["statement"],
                                 "type": card.get("type", "")}, ensure_ascii=False),
                criteria=CRITERIA_SHAPE, note=CRITERIA_NOTE))
            criteria = self._criteria(said.get("criteria"))
            if criteria:
                self._set_criteria(card, criteria, str(said.get("approach") or ""))
                self._note("criteria written", card=card["title"], count=len(criteria))

    def _round(self, fix: Optional[List[Dict[str, Any]]] = None) -> str:
        number = int(self.plan.get("round") or 0) + 1
        self._round_writes = set()
        self._stage(f"round {number}", f"Round {number}" + (
            " - fixing what the final review found" if fix else ""))
        self._write_criteria()
        if not self._going():
            return "paused"
        parts = [self._brief()]
        findings = self._last_findings()
        if findings:
            parts.append("WHAT THE REVIEWER SAID OF THE LAST ROUND:\n" + findings)
        todo = self.plan.pop("resumed_from", None)
        if todo:
            parts.append("THE RUN WAS PAUSED; ITS TO-DO LIST - START FROM HERE:\n"
                         + _plain(json.dumps(todo, ensure_ascii=False))[:6000])
        if int(self.plan.get("stalls") or 0) >= STEP_BACK_AT:
            parts.append(STEP_BACK)
        if fix:
            parts.append(FIX_FINDINGS.format(findings="\n".join(
                f"  - ({one.get('severity', '')}) {_plain(one.get('say', ''))}"
                f"{' - ' + one['file'] if one.get('file') else ''}" for one in fix)))
        parts.append(WORK_ROUND)
        messages = [{"role": "user", "content": "\n\n".join(parts)}]
        summary = self._ask(self._system(), messages, self.tools, writing=True,
                            rounds=WORK_TOOL_ROUNDS, pause=PAUSE)
        if summary.get("interrupted"):
            self._interrupted(number, summary)
            return "paused"
        if not summary and not self._going():
            return "paused"
        problems = self._gate(summary)
        tries = 0
        while problems and tries < GATE_RETRIES and self._going():
            tries += 1
            self._note("checks failed", round=number, problems=problems[:8])
            messages.append({"role": "user", "content": GATE.format(
                problems="\n".join(f"  - {one}" for one in problems))})
            summary = self._ask(self._system(), messages, self.tools, writing=True,
                                rounds=WORK_TOOL_ROUNDS, pause=PAUSE)
            if summary.get("interrupted"):
                self._interrupted(number, summary)
                return "paused"
            problems = self._gate(summary)
        root, cards = self._tree()
        refs = self._apply_parts(summary, root, cards)
        card = self._card_named(summary.get("card"), refs, cards) or root
        if problems:
            self._record_round(card, number, summary, problems, {}, full=False)
            return self._after_stall(number)
        claims = len([one for one in summary.get("claims") or [] if isinstance(one, dict)])
        self._event("run", "gate", f"Round {number}: the summary passed the checks - "
                    f"{claims} claim{'s' if claims != 1 else ''}, each with evidence that "
                    "exists; the contract untouched", ok=True)
        full = number % FULL_REVIEW_EVERY == 0
        message = commit_message(
            summary, summary.get("result") or summary.get("objective") or card["title"],
            [one.get("claim") for one in summary.get("claims") or []
             if isinstance(one, dict)])
        review = Review(self, self._card_profile(card, summary),
                        self._reviewer_context(card, recent=True),
                        same_as=f"round:{card.get('id', '')}")
        judged = review.ask(REVIEW_ROUND.format(
            summary=json.dumps({k: v for k, v in summary.items() if k != "written_by"},
                               ensure_ascii=False)[:14000],
            full=FULL_REVIEW if full else "",
            needs_user=NEEDS_USER, for_user=FOR_USER_NOTE,
            stop=',\n  "stop": false' if full else ""))
        self.plan["findings"] = judged
        self._judge_promises(judged, number)
        self._for_user(judged, f"round {number}")
        unmet = self._unmet()
        if judged.get("complete") and unmet:
            judged["complete"] = False
            self._note("not complete", round=number,
                       why=f"the core promises {', '.join(unmet)} do not hold yet")
        self._apply_review(card, number, summary, judged, root)
        self._record_round(card, number, summary, [], judged, full=full)
        self._note("round reviewed", round=number, card=card["title"],
                   progress=judged.get("progress"),
                   findings=[f.get("say") for f in judged.get("findings") or []][:6])
        progress = str(judged.get("progress") or "").lower()
        # KEY POINT: a round the reviewer found advanced, and every full
        # review whatever it found.
        if full or progress == "advanced":
            # NAMED FOR WHAT IT CHANGED: the commit the builder wrote with
            # its round - or, when it wrote none, the round's own words:
            # what came of it, and the claims now true. The round and what
            # the reviewer found of it are fields of their own.
            self._version("full review" if full else "round",
                          f"Round {number}{' (full review)' if full else ''}: "
                          f"{card['title']} - {progress or 'reviewed'}",
                          message=message,
                          round=number, progress=progress or "reviewed",
                          full_review=full)
        if judged.get("complete") and not fix:
            self.plan["stalls"] = 0
            return "complete"
        if full and judged.get("stop"):
            self.stop_reason = "the reviewer ended it at a full review"
            return "stop"
        if progress in ("stalled", "regressed"):
            return self._after_stall(number)
        self.plan["stalls"] = 0
        if self._all_blocked():
            return "blocked"
        return "next"

    def _after_stall(self, number: int) -> str:
        self.plan["stalls"] = int(self.plan.get("stalls") or 0) + 1
        if self.plan["stalls"] >= STALLS_TO_STOP:
            self.stop_reason = f"{STALLS_TO_STOP} rounds in a row without progress"
            return "stalled"
        return "blocked" if self._all_blocked() else "next"

    def _all_blocked(self) -> bool:
        """Every deliverable not done is set aside: nothing left to move."""
        root, cards = self._tree()
        deliverables = [one for one in cards.values() if one.get("parent_id") == root["id"]]
        if not deliverables or not self.plan.get("blocked"):
            return False
        return all(one["status"] in ("done", "set_aside") for one in deliverables) \
            and any(one["status"] == "set_aside" for one in deliverables)

    def _card_named(self, said: Any, refs: Dict[str, Any],
                    cards: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        name = str(said or "")
        return refs.get(name) or cards.get(name)

    def _resolve(self, path: str) -> Path:
        target = Path(path).expanduser()
        return target if target.is_absolute() else self.folder / target

    def _gate(self, summary: Dict[str, Any]) -> List[str]:
        """The mechanical checks on a round's summary, before anyone reads
        it: that there is one, that it names a card, that every claim has
        evidence that exists - and that the contract is as approved."""
        problems: List[str] = []
        problems += self._contract_intact()
        if not summary or not isinstance(summary, dict):
            return problems + ["no summary came back: return the round JSON object"]
        root, cards = self._tree()
        made = {str(one.get("ref") or "") for one in summary.get("cards") or []
                if isinstance(one, dict)}
        name = str(summary.get("card") or "")
        blocked = summary.get("blocked") if isinstance(summary.get("blocked"), dict) else {}
        if not name or (name not in cards and name not in made):
            problems.append(f"\"card\" must be the id of a card in the tree, not {name!r}")
        if blocked and not str(blocked.get("missing") or "").strip():
            problems.append("\"blocked\" must say what is missing")
        unmet = self._unmet()
        if unmet and (name in cards or name in made):
            if name in cards:
                tier = self._tier_of(cards[name], cards)
            else:
                entry = next(one for one in summary.get("cards") or []
                             if isinstance(one, dict) and str(one.get("ref") or "") == name)
                tier = (tier_of(entry["tier"]) if entry.get("tier") else
                        self._tier_of(cards.get(str(entry.get("parent") or "")), cards))
            if tier == "polish":
                problems.append(f"card {name} is polish, and the core promises "
                                f"{', '.join(unmet)} do not hold yet: work on a core "
                                "card - or the supporting work one needs - first")
        claims = [one for one in summary.get("claims") or [] if isinstance(one, dict)]
        if not claims and not blocked:
            problems.append("no claims: say what is now true, each with evidence")
        for number, claim in enumerate(claims, start=1):
            evidence = [one for one in claim.get("evidence") or [] if isinstance(one, dict)]
            if not evidence:
                problems.append(f"claim {number} has no evidence")
            for one in evidence:
                if one.get("path"):
                    from ...runtime.filesystem import is_app_code, is_closed, is_sealed
                    if is_sealed(self._resolve(str(one["path"])), self.runtime.policy):
                        problems.append(f"claim {number}: {one['path']} is in a "
                                        "folder closed to this work, which it may not read")
                    elif is_app_code(self._resolve(str(one["path"])), self.runtime.policy) \
                            or is_closed(self._resolve(str(one["path"])), self.runtime.policy):
                        problems.append(f"claim {number}: {one['path']} is where no "
                                        "agent may read - the application's own code, "
                                        "or the run logs")
                    elif not self._resolve(str(one["path"])).exists():
                        problems.append(f"claim {number}: {one['path']} does not exist")
                elif one.get("command"):
                    if not str(one.get("output") or "").strip():
                        problems.append(f"claim {number}: the output of "
                                        f"{one['command']!r} is missing")
                else:
                    problems.append(f"claim {number}: evidence is a path or a command "
                                    "with its output")
        return problems

    def _contract_intact(self) -> List[str]:
        """The contract file is the approved one - and if code changed it,
        it is put back."""
        contract = self.plan.get("contract")
        if not contract:
            return []
        approved = json.dumps(contract, ensure_ascii=False, indent=2)
        try:
            now = self.contract_path.read_text(encoding="utf-8")
        except OSError:
            now = ""
        if now == approved:
            return []
        self.runtime.write_text(str(self.contract_path.relative_to(self.folder)), approved)
        return ["the contract file was changed - it is locked, and was put back"]

    def _apply_parts(self, summary: Dict[str, Any], root: Dict[str, Any],
                     cards: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """The parts the round made, under their deliverables. A card asked
        for directly under the root is a new deliverable: an amendment, held
        for the reviewer."""
        parts, asked = [], []
        for one in summary.get("cards") or []:
            if not isinstance(one, dict):
                continue
            parent = str(one.get("parent") or "root")
            if parent in ("", "root", root["id"]):
                asked.append(one)
            else:
                parts.append(one)
        amend = summary.get("amend") if isinstance(summary.get("amend"), dict) else {}
        held = [one for one in amend.get("deliverables") or [] if isinstance(one, dict)]
        if asked or held:
            summary["amend"] = {"deliverables": held + asked,
                                "why": str(amend.get("why") or "")}
        refs = self.writer._apply_cards(self.work_unit, {"cards": parts}, root, cards,
                                        summary.get("written_by") or {}) if parts else {}
        self._set_tiers(refs, [one for one in parts if one.get("tier")], [])
        for changed in summary.get("cards_changed") or []:
            if not isinstance(changed, dict):
                continue
            card = refs.get(str(changed.get("card") or "")) or cards.get(
                str(changed.get("card") or ""))
            # The agent may set aside its own parts; deliverables and the
            # goal are the contract's.
            if card is not None and card.get("parent_id") not in ("", root["id"]) \
                    and str(changed.get("status") or "") == "set_aside":
                self.store.update_card(card["id"], {
                    "status": "set_aside", "status_why": str(changed.get("why") or "")[:500]})
        return refs

    def _apply_review(self, card: Dict[str, Any], number: int, summary: Dict[str, Any],
                      judged: Dict[str, Any], root: Dict[str, Any]) -> None:
        if judged.get("card_done") and card["id"] != root["id"]:
            self.store.update_card(card["id"], {"status": "done",
                                                "status_why": "the reviewer accepted it"})
        elif card["id"] != root["id"] and card["status"] == "open":
            self.store.update_card(card["id"], {"status": "working"})
        _root, cards = self._tree()
        for finding in judged.get("findings") or []:
            if not isinstance(finding, dict) or finding.get("type") != "queued":
                continue
            said = finding.get("card") if isinstance(finding.get("card"), dict) else {}
            title = str(said.get("title") or finding.get("say") or "")[:300]
            parent = cards.get(card.get("parent_id") or "") or root
            self.writer._apply_cards(self.work_unit, {"cards": [{
                "parent": parent["id"], "title": title,
                "statement": str(said.get("statement") or finding.get("say") or ""),
                "type": card.get("type") or "other",
                "why": {"why": "queued by the reviewer", "from": "inferred"}}]},
                root, cards, {"by": "reviewer"})
        amend = summary.get("amend") or {}
        if amend.get("deliverables"):
            verdict = str(judged.get("amendment") or "rejected").lower()
            self.plan.setdefault("amendments", []).append({
                "round": number, "deliverables": [one.get("title") for one in
                                                  amend["deliverables"]],
                "why": amend.get("why", ""), "verdict": verdict})
            if verdict.startswith("approve"):
                self.writer._apply_cards(self.work_unit, {"cards": [
                    dict(one, parent="root") for one in amend["deliverables"]]},
                    root, cards, summary.get("written_by") or {})
                contract = self.plan.get("contract") or {}
                contract.setdefault("amended", []).append(
                    {"round": number, "added": [one.get("title") for one in
                                                amend["deliverables"]]})
                self.runtime.write_text(str(self.contract_path.relative_to(self.folder)),
                                        json.dumps(contract, ensure_ascii=False, indent=2))
        blocked = summary.get("blocked") if isinstance(summary.get("blocked"), dict) else {}
        if blocked and judged.get("blocked_confirmed"):
            target = cards.get(str(blocked.get("card") or "")) or card
            if target["id"] != root["id"]:
                self.store.update_card(target["id"], {
                    "status": "set_aside",
                    "status_why": f"blocked: {blocked.get('missing', '')}"[:500]})
                self.plan.setdefault("blocked", []).append({
                    "card": target["id"], "title": target["title"],
                    "missing": str(blocked.get("missing") or ""), "round": number})
        support = {int(one.get("n", -1)): one for one in judged.get("decisions") or []
                   if isinstance(one, dict) and str(one.get("n", "")).lstrip("-").isdigit()}
        for n, decision in enumerate(summary.get("decisions") or []):
            if not isinstance(decision, dict):
                continue
            backed = support.get(n) or {}
            self.plan.setdefault("decisions", []).append({
                "round": number, "card": card["title"],
                "about": str(decision.get("about") or ""),
                "chose": str(decision.get("chose") or ""),
                "over": str(decision.get("over") or ""),
                "why": str(decision.get("why") or ""),
                "support": str(backed.get("support") or "none"),
                "because": backed.get("because") or []})

    def _record_round(self, card: Dict[str, Any], number: int, summary: Dict[str, Any],
                      problems: List[str], judged: Dict[str, Any], full: bool,
                      interrupted: bool = False) -> None:
        """The round, kept in the work's tree as the agent's stage: its
        claims and evidence, the checks, and the review."""
        from ...heartbeat.experience.store import MEDIA, STAGE_KINDS
        kind = str(summary.get("kind") or "execution").lower()
        kind = kind if kind in STAGE_KINDS else "execution"
        medium = card.get("type") or "other"
        files = sorted({str(one.get("path")) for claim in summary.get("claims") or []
                        if isinstance(claim, dict)
                        for one in claim.get("evidence") or []
                        if isinstance(one, dict) and one.get("path")})[:16]
        stamp = _now()
        verdict = ("interrupted" if interrupted else "failed the checks" if problems
                   else str(judged.get("progress") or ""))
        review = ([] if not judged else [dict(
            judged, at=stamp, on="the round", round=number,
            verdict=verdict, comments=[{"say": f"({f.get('type', '')}) {f.get('say', '')}",
                                        "because": f.get("because") or []}
                                       for f in judged.get("findings") or []
                                       if isinstance(f, dict)])])
        previous = [s for s in self.store.stages([self.unit["id"]])
                    if s.get("card_id") == card["id"]
                    and not (s.get("notes") or {}).get("review_of")]
        made = self.store.add_stage({
            "task_id": self.unit["id"], "card_id": card["id"], "kind": kind,
            "medium": medium if medium in MEDIA else "other",
            "summary": f"Round {number}: {summary.get('objective') or ''} - "
                       f"{summary.get('result') or ''}"[:2000],
            "deliverable": {"type": medium if medium in MEDIA else "other",
                            "excerpt": str(summary.get("result") or "")[:1500],
                            "references": [{"location": one, "what": "evidence",
                                            "kind": "file"} for one in files]},
            "evaluation": {"verdict": verdict,
                           "criteria": [str(one) for one in judged.get("checked") or []][:10],
                           "words": [f.get("say", "") for f in judged.get("findings") or []
                                     if isinstance(f, dict)][:10]},
            "states": [], "began": stamp, "ended": stamp,
            "work": {"decision": {"about": str(summary.get("objective") or ""),
                                  "chose": str(summary.get("result") or "")[:1200],
                                  "by": "agent", "why": ""},
                     "output": {"what": str(summary.get("result") or "")[:1200]}},
            "notes": {"run": self.id, "round": number, "full_review": full,
                      "claims": summary.get("claims") or [],
                      "open": summary.get("open") or [],
                      "checks_failed": problems, "reviews": review,
                      "interrupted": interrupted},
            "written_by": dict(summary.get("written_by") or {}, at=stamp)})
        if previous:
            self.store.add_edge(made["id"], previous[-1]["id"], "continues")
        self._link_round(card, number, made, files, judged, stamp)
        self.plan["round"] = number
        self.plan.setdefault("rounds", []).append({
            "n": number, "card": card["title"], "objective": summary.get("objective", ""),
            "progress": verdict, "full": full, "stage": made["id"], "at": stamp})
        self.owner.save(self)

    def _link_round(self, card: Dict[str, Any], number: int, made: Dict[str, Any],
                    files: List[str], judged: Dict[str, Any], stamp: str) -> None:
        """THE ROUND IN THE TREE, LINKED AS THE WORK WENT: the reviewer's
        verdict a stage of its own, which EVALUATES the round; the next round
        on the card REVISES what it said; and a round whose evidence is a
        file another card's round wrote DEPENDS ON that round."""
        reviewed = self.plan.setdefault("review_stages", {})
        before = reviewed.get(card["id"])
        if before:
            self.store.add_edge(made["id"], before, "revises",
                                "the round answers the last review of its card")
        written = self.plan.setdefault("written_by_stage", {})
        own = {_where(one) for one in getattr(self, "_round_writes", set())}
        for path in files:
            where = _where(str(self._resolve(path)))
            source = written.get(where)
            if where not in own and source and source.get("card") != card["id"]:
                self.store.add_edge(made["id"], source["stage"], "depends_on",
                                    f"its evidence {Path(path).name}")
        for where in own:
            written[where] = {"stage": made["id"], "card": card["id"]}
        if len(written) > 2000:
            for key in list(written)[:len(written) - 2000]:
                written.pop(key, None)
        if not judged:
            return
        findings = [f for f in judged.get("findings") or [] if isinstance(f, dict)]
        progress = str(judged.get("progress") or "reviewed")
        from ...heartbeat.experience.store import MEDIA
        medium = card.get("type") or "other"
        verdict = self.store.add_stage({
            "task_id": self.unit["id"], "card_id": card["id"], "kind": "evaluation",
            "medium": medium if medium in MEDIA else "other",
            "summary": (f"Review of round {number}: {progress}"
                        + (" - " + "; ".join(str(f.get("say") or "") for f in findings[:4])
                           if findings else ""))[:2000],
            "evaluation": {"verdict": progress,
                           "criteria": [str(one) for one in judged.get("checked") or []][:10],
                           "words": [str(f.get("say") or "") for f in findings][:10]},
            "states": [], "began": stamp, "ended": stamp,
            "work": {"decision": {"about": f"whether round {number} moved the card",
                                  "chose": progress, "by": "agent",
                                  "why": "; ".join(str(f.get("say") or "")
                                                   for f in findings[:3])[:800]}},
            "notes": {"run": self.id, "round": number, "review_of": made["id"],
                      "findings": findings[:20],
                      "promises": judged.get("promises") or [],
                      "complete": bool(judged.get("complete"))},
            "written_by": dict(judged.get("written_by") or {}, at=stamp)})
        self.store.add_edge(verdict["id"], made["id"], "evaluates", f"round {number}")
        reviewed[card["id"]] = verdict["id"]

    def _interrupted(self, number: int, summary: Dict[str, Any]) -> None:
        root, cards = self._tree()
        card = self._card_named(summary.get("card"), {}, cards) or root
        self.plan["interrupted"] = {"round": number, "card": card["title"],
                                    "objective": summary.get("objective", ""),
                                    "done_so_far": summary.get("result", ""),
                                    "left": summary.get("open") or []}
        self._record_round(card, number, summary, [], {}, full=False, interrupted=True)

    # -- C. the final review --------------------------------------------------------------------
    def _final(self) -> str:
        """Everything the work changed, reviewed as a whole. Returns clean,
        or fix - with the findings to fix in the plan."""
        self._stage("final review", "The final review: the reviewer looks over "
                    "everything the work changed, as a whole")
        files = [one for one in self.plan.get("changed_files") or []
                 if Path(one).exists()]
        root, _cards = self._tree()
        tested = self._black_box(files)
        if not self._going(work=False):
            return "clean"
        review = Review(self, {"kinds": ["evaluation", "delivery"],
                               "text": root.get("statement", ""),
                               "tasks": self.project_tasks},
                        self._reviewer_context(None))
        judged = review.ask(REVIEW_FINAL.format(files="\n".join(
            f"  {one}" for one in files[:200]) or "  (no files)",
            for_user=FOR_USER_NOTE) + (
            "\n\nA TESTER WHO NEVER SAW HOW IT WAS BUILT USED IT AS THE USER WOULD:\n"
            + json.dumps(tested, ensure_ascii=False)[:8000] if tested else ""))
        found = [one for one in judged.get("findings") or [] if isinstance(one, dict)]
        found += [dict(one, by="tester") for one in (tested or {}).get("findings") or []
                  if isinstance(one, dict)]
        to_fix = [one for one in found
                  if str(one.get("severity") or "").lower() in ("high", "medium")]
        self.plan["final_review"] = dict(judged, to_fix=to_fix, at=_now())
        self._for_user(judged, "the final review")
        self._note("final review", findings=len(judged.get("findings") or []),
                   to_fix=len(to_fix))
        # KEY POINT: the final review done.
        firsts = "; ".join(_clip(one.get("say"), 40) for one in to_fix[:2])
        self._version("final review", f"Final review: {len(to_fix)} to fix",
                      message=commit_message(
                          None, f"Final review: {len(to_fix)} to fix"
                          + (f" — {firsts}" if firsts else "") if to_fix
                          else "Final review: nothing to fix",
                          [one.get("say") for one in to_fix]))
        if not to_fix:
            return "clean"
        if int(self.plan.get("fix_rounds") or 0) >= FIX_ROUNDS:
            # NOT DONE WHILE A CORE PROMISE IS BROKEN: the fixes ran out with
            # the tester or the reviewer still finding one that does not
            # hold - the run ends unfinished, saying which, with its to-do.
            unmet = self._unmet()
            if unmet:
                self.stop_reason = (f"after {FIX_ROUNDS} rounds of fixes the core promises "
                                    f"{', '.join(unmet)} still do not hold")
                return "unmet"
            return "clean"
        self.plan["fix_rounds"] = int(self.plan.get("fix_rounds") or 0) + 1
        return "fix"

    def _black_box(self, files: List[str]) -> Dict[str, Any]:
        """The work used as its user would, by a context that never saw how
        it was built (TESTER_SYSTEM): what it found of each core promise -
        taken as the promises' state - and its findings. Nothing when the
        contract makes no promise."""
        promises = self._promises()
        if not promises:
            return {}
        if getattr(self, "tester_tools", None) is None:
            self.tester_tools = WorkTools(self._checking_runtime(),
                                          log=lambda name, said: self._tool_used(
                                              name, said, who="reviewer"))
            self.tester_tools.locked = list(self.tools.locked)
            self.tester_tools.runs_code = ("run_python",)
        self._event("reviewer", "stage", "The tester uses the work as the user would")
        words = "\n".join([self.instruction or "", *self._branch_words()]).strip()
        said = self._ask(
            TESTER_SYSTEM.replace("{checks}", str(self.checks_folder)) + "\n\n" + zone_rule()
            + native_rule(),
            [{"role": "user", "content": TEST_AS_USER.format(
                words=words or "(carry the work forward)",
                promises="\n".join(
                    f"  {one['id']}: {one['promise']}"
                    + (f"\n    tried by: {one['real_condition']}" if one.get("real_condition") else "")
                    + ("\n    must survive: " + "; ".join(one["fails_if"]) if one.get("fails_if") else "")
                    for one in promises),
                files="\n".join(f"  {one}" for one in files[:200]) or "  (no files)",
                needs_user=NEEDS_USER, for_user=FOR_USER_NOTE)}],
            self.tester_tools, writing=False, work=False, who="reviewer")
        tested = {key: said.get(key) for key in ("promises", "findings") if said.get(key)}
        self._judge_promises(tested, int(self.plan.get("round") or 0))
        self._for_user(said, "the tester")
        self.plan["black_box"] = dict(tested, at=_now())
        self._note("tested as the user would",
                   holds=[one.get("id") for one in tested.get("promises") or []
                          if isinstance(one, dict) and one.get("holds") == "yes"],
                   unmet=self._unmet())
        return tested

    # -- D. the wrap-up -------------------------------------------------------------------------
    def _wrap_up(self, ended: str) -> None:
        finished = ended == "complete"
        self.status = {"complete": DONE, "paused": NOT_FINISHED, "stalled": STALLED,
                       "stop": STOPPED_BY_REVIEW, "blocked": BLOCKED,
                       "no contract": NO_CONTRACT}.get(ended, NOT_FINISHED)
        if not finished and self.status != NO_CONTRACT:
            self._write_todo()
        # An amendment not approved did no work: no new deck to make.
        if self.status != NO_CONTRACT and ended != "not amended" \
                and self._going(work=False) and self._has_results():
            self._deck(finished)
            self._readme(finished)
        if finished:
            self.plan["phase"] = "done"
            root, _cards = self._tree()
            self.store.update_card(root["id"], {"status": "done",
                                                "status_why": "the reviewer accepted it"})
        self.final = dict(self.final, done=finished, ended=ended,
                          reason=self.stop_reason)
        self._write_report()
        self._note("ended", status=self.status, reason=self.stop_reason)

    def _readme(self, finished: bool, alone: bool = False) -> Optional[str]:
        """THE DELIVERABLE'S README, in the work's folder: what was made,
        where, how to check it and how to use it. Not over a README of the
        user's: beside it, then. The relative path written, or None.
        `alone`: a single model's run, which keeps no cards - told what was
        asked, its report and the files it changed, not the tree."""
        if not self._going(work=False):
            return None
        self._stage("readme", "The README of what was made: how to check it and use it")
        brief = ("\n".join([f"WHAT WAS ASKED: {self.instruction}",
                            f"ITS REPORT: {self.plan.get('last_report') or '(none)'}",
                            "FILES IT CHANGED:",
                            *[f"  - {one}" for one in (self.plan.get("changed_files") or [])[:60]]])
                 if alone else self._brief())
        messages = [{"role": "user", "content": brief + "\n\n" + README_PROMPT.format(
            unfinished="" if finished else (
                "\nTHE WORK IS NOT FINISHED: say what exists so far, and what is left."))}]
        try:
            said = self._ask(self._system(), messages, self.tools, writing=False, work=False,
                             language=in_display("the README"))
        except Exception:        # pragma: no cover - the report is there anyway
            log.warning("the README of %s could not be written", self.id, exc_info=True)
            return None
        text = str((said or {}).get("readme") or "").strip()
        if not text:
            return None
        name = "README.md"
        try:
            there = self.runtime.read_text(name)
        except Exception:
            there = None
        if there is not None and not str(there).startswith(README_MARK):
            name = "README-deliverable.md"
        self._write(name, README_MARK + "\n\n" + text + "\n")
        self.final = dict(self.final, readme=name)
        self._event("run", "readme", f"Wrote {name}: what was made, where, how to check "
                    "it and how to use it")
        return name

    def _has_results(self) -> bool:
        return bool(self.plan.get("contract")) and any(
            Path(one).exists() for one in self.plan.get("changed_files") or [])

    def _write_todo(self) -> None:
        """What is left, worked out from the tree - no model needed: each
        open card and what it must meet, what the interrupted round was
        doing, what is blocked and on what, and the next step."""
        root, cards = self._tree()
        open_cards = []
        for card in self._open(cards, root):
            checked = card.get("checked_by") or {}
            open_cards.append({
                "card": card["id"], "title": card["title"], "status": card["status"],
                "done_means": card["statement"],
                "criteria": [one["text"] for one in checked.get("criteria") or []],
                "queued": (card.get("why") or {}).get("why") == "queued by the reviewer"})
        findings = [one.get("say", "") for one in
                    (self.plan.get("findings") or {}).get("findings") or []
                    if isinstance(one, dict) and one.get("type") in ("mainline", "blocking")]
        interrupted = self.plan.get("interrupted") or {}
        todo = {"open": open_cards, "blocked": self.plan.get("blocked") or [],
                "interrupted": interrupted,
                "next": (interrupted.get("left") or findings or
                         [f"{one['title']}: {one['done_means']}" for one in open_cards])[:6],
                "final_review": (self.plan.get("final_review") or {}).get("to_fix") or []
                if self.plan.get("phase") == "final" else []}
        self.plan["todo"] = todo
        lines = [f"# To do - {self.unit.get('name', '')}", "",
                 f"Paused: {self.stop_reason or self.status}", ""]
        if interrupted:
            lines += ["## The round that was cut off",
                      f"- {interrupted.get('card')}: {interrupted.get('objective')}",
                      f"- done so far: {interrupted.get('done_so_far')}"]
            lines += [f"- left: {one}" for one in interrupted.get("left") or []]
            lines.append("")
        lines.append("## Next")
        lines += [f"- {one}" for one in todo["next"]] or ["- (nothing named)"]
        lines += ["", "## Open cards"]
        for one in open_cards:
            lines.append(f"- **{one['title']}**{' (queued)' if one['queued'] else ''} "
                         f"- {one['done_means']}")
            lines += [f"  - {text}" for text in one["criteria"]]
        if todo["blocked"]:
            lines += ["", "## Blocked"]
            lines += [f"- {one['title']}: needs {one['missing']}" for one in todo["blocked"]]
        if todo["final_review"]:
            lines += ["", "## Left from the final review"]
            lines += [f"- ({one.get('severity')}) {one.get('say')}"
                      for one in todo["final_review"]]
        self._write(f"runs/{self.id}/todo.md", "\n".join(lines))

    def _write(self, relative: str, text: str) -> None:
        try:
            self.runtime.write_text(relative, text)
        except Exception:        # pragma: no cover - the record is in the plan
            log.debug("could not write %s", relative, exc_info=True)

    def _deck(self, finished: bool) -> None:
        """The deck of the results: made by the work agent from the files,
        checked by code, checked by the reviewer against the files and how
        the user likes results shown, and rendered."""
        from .deck import render
        self._stage("deck", "The results deck: slides of what the work produced"
                    + ("" if finished else " so far"))
        prompt = MAKE_DECK.format(unfinished="" if finished else (
            "\nTHE WORK IS NOT FINISHED: show what exists so far, as it is."))
        messages = [{"role": "user", "content": self._brief() + "\n\n" + prompt}]
        deck = self._ask(self._system(), messages, self.tools, writing=False, work=False)
        problems = self._gate_deck(deck)
        tries = 0
        while problems and tries < GATE_RETRIES and self._going(work=False):
            tries += 1
            messages.append({"role": "user", "content": GATE_DECK.format(
                problems="\n".join(f"  - {one}" for one in problems))})
            deck = self._ask(self._system(), messages, self.tools, writing=False,
                             work=False)
            problems = self._gate_deck(deck)
        if not deck.get("deliverables") and not deck.get("results"):
            return
        root, _cards = self._tree()
        review = None
        for _ in range(DECK_REVIEWS):
            if problems or not self._going(work=False):
                break
            if review is None:
                review = Review(self, {"kinds": ["delivery"], "type": "slides",
                                       "text": f"{root.get('statement', '')} results",
                                       "tasks": self.project_tasks},
                                self._reviewer_context(None), work=False)
            judged = review.ask(REVIEW_DECK.format(deck=json.dumps(
                {k: v for k, v in deck.items() if k != "written_by"},
                ensure_ascii=False)[:14000]))
            self.plan["deck_review"] = judged
            comments = [_clip(one.get("say"), 120) for one in judged.get("comments") or []
                        if isinstance(one, dict)][:3]
            self._event("reviewer", "verdict", f"The deck: {judged.get('verdict') or 'checked'}"
                        + (" - " + "; ".join(comments) if comments else ""),
                        verdict=judged.get("verdict"))
            if str(judged.get("verdict") or "").lower().startswith("acc"):
                break
            messages.append({"role": "user", "content": REVISE_DECK.format(
                review=json.dumps(judged.get("comments") or [], ensure_ascii=False)[:6000])})
            revised = self._ask(self._system(), messages, self.tools, writing=False,
                                work=False)
            if not self._gate_deck(revised):
                deck = revised
        kept = {k: v for k, v in deck.items() if k not in ("written_by", "interrupted")}
        kept.update(finished=finished, goal=root.get("statement", ""),
                    checks_failed=problems)
        self.plan["deck"] = kept
        self._write(f"runs/{self.id}/presentation.html", render(kept))
        self._note("deck made", slides=2 + len(kept.get("results") or []))
        # KEY POINT: the deck made.
        self._version("deck", "The results deck" + ("" if finished else " - not finished"),
                      message=commit_message(
                          None, "Results deck" + ("" if finished else " (not finished)"),
                          [one.get("title") for one in kept.get("results") or []
                           if isinstance(one, dict)]))

    def _gate_deck(self, deck: Dict[str, Any]) -> List[str]:
        problems: List[str] = []
        if not deck:
            return ["no deck came back: return the deck JSON object"]
        _root, cards = self._tree()
        for one in deck.get("deliverables") or []:
            if not isinstance(one, dict):
                continue
            if not self._resolve(str(one.get("location") or "")).exists() \
                    or not str(one.get("location") or "").strip():
                problems.append(f"deliverable {one.get('name')!r}: "
                                f"{one.get('location')!r} does not exist")
            if str(one.get("card") or "") not in cards:
                problems.append(f"deliverable {one.get('name')!r} names no card of the tree")
            if not str(one.get("what") or "").strip():
                problems.append(f"deliverable {one.get('name')!r} does not say what it is")
        for result in deck.get("results") or []:
            if not isinstance(result, dict):
                continue
            figure = str(result.get("figure") or "")
            if figure and not self._resolve(figure).exists():
                problems.append(f"result {result.get('title')!r}: figure {figure} "
                                "does not exist")
            for variable in result.get("variables") or []:
                if isinstance(variable, dict) and not str(variable.get("source") or "").strip():
                    problems.append(f"result {result.get('title')!r}: variable "
                                    f"{variable.get('name')!r} gives no source")
        return problems

    def _write_report(self) -> None:
        """How the run went - for the user, apart from the deck: the
        decisions made for them, what backs each criterion, the rounds, the
        amendments, what blocked."""
        root, cards = self._tree()
        total = {k: int((self.plan.get("total") or {}).get(k, 0)) + int(self.used.get(k, 0))
                 for k in ("calls", "tokens_in", "tokens_out")}
        lines = [f"# Report - {self.unit.get('name')}", "",
                 f"Run {self.id}: {self.status}"
                 + (f" ({self.stop_reason})" if self.stop_reason else ""), "",
                 f"Asked: {self.instruction or '(carry the work forward)'}", "",
                 f"This session: {self.used['calls']} model calls, "
                 f"{self.used['tokens_in'] + self.used['tokens_out']} tokens "
                 f"({self.used['tokens_in']} in, {self.used['tokens_out']} out), "
                 f"{round(self._minutes(), 1)} minutes"
                 + (f" (and {round(self.waited / 60, 1)} waiting for you)"
                    if self.waited >= 1 else "")
                 + (f" (and {round(self._paused_seconds() / 60, 1)} paused by you)"
                    if self._paused_seconds() >= 1 else ""), ""]
        decisions = self.plan.get("decisions") or []
        lines.append("## Decisions made for you")
        lines += [f"- {one['about']}: chose {one['chose']} over {one['over'] or '-'} "
                  f"({one['support']} support from your past work"
                  f"{'; ' + ', '.join(one['because']) if one.get('because') else ''})"
                  f" - {one['why']}" for one in decisions] or ["- none"]
        book = self.plan.get("packages") or {}
        if book.get("requests"):
            lines += ["", "## Installed packages ("
                      + ("you approved each request" if book.get("mode") == ASK
                         else "installed without asking you - Auto") + ")"]
            for one in book["requests"]:
                got = ", ".join(f"{p['name']} {p['version']}"
                                for p in one.get("installed") or [])
                lines.append(f"- {', '.join(one.get('packages') or [])}: "
                             f"{one.get('status')}{' - ' + got if got else ''}"
                             f"{' - ' + one['error'] if one.get('error') else ''}"
                             f" (why: {one.get('why', '')})")
        lines += ["", "## What each deliverable had to meet"]
        for card in cards.values():
            if card.get("parent_id") != root["id"]:
                continue
            checked = card.get("checked_by") or {}
            lines.append(f"- **{card['title']}** ({card['status']})")
            lines += [f"  - {one['text']} - backed by {one['backed_by']}"
                      for one in checked.get("criteria") or []]
        lines += ["", "## Rounds"]
        lines += [f"{one['n']}. {one['card']}: {one['objective']} - {one['progress']}"
                  f"{' (full review)' if one.get('full') else ''}"
                  for one in self.plan.get("rounds") or []] or ["- none"]
        if self.plan.get("amendments"):
            lines += ["", "## Amendments"]
            lines += [f"- round {one['round']}: {', '.join(map(str, one['deliverables']))}"
                      f" - {one['verdict']}" for one in self.plan["amendments"]]
        if self.plan.get("blocked"):
            lines += ["", "## Blocked"]
            lines += [f"- {one['title']}: needs {one['missing']}"
                      for one in self.plan["blocked"]]
        lines += ["", "## For you to check or do"]
        lines += [f"- {one['do']}" + (f" ({one['promise']})" if one.get("promise") else "")
                  + f" - from {one['milestone']}" + (f": {one['why']}" if one.get("why") else "")
                  for one in self.plan.get("for_user") or []] or [
            "- nothing: the work checked everything itself"]
        final = self.plan.get("final_review") or {}
        if final.get("findings"):
            lines += ["", "## Final review"]
            lines += [f"- ({one.get('severity')}) {one.get('say')}"
                      for one in final["findings"] if isinstance(one, dict)]
        self.final = dict(self.final, report="\n".join(lines[:3]),
                          rounds=len(self.plan.get("rounds") or []), total=total)
        self._write(f"runs/{self.id}/report.md", "\n".join(lines))

    # -- the reviewer's contexts ----------------------------------------------------------------
    def _contract_profile(self, words: str = "") -> Dict[str, Any]:
        # Asked of every question to the record, a run's too that has not
        # been given its work yet: what it does not have, it does not say.
        return {"kinds": ["goal", "plan"],
                "text": " ".join(filter(None, [(getattr(self, "unit", None) or {}).get("name", ""),
                                               getattr(self, "instruction", ""), words])),
                "tasks": getattr(self, "project_tasks", [])}

    def _card_profile(self, card: Dict[str, Any], summary: Dict[str, Any]
                      ) -> Dict[str, Any]:
        """The card - and what the round did on it - as a question to the
        record: its kind, type, place in its card, level, environment,
        project and words."""
        from ...heartbeat.experience.index import stage_terms
        from ...heartbeat.experience.retrieve import PLACE_DEPTH, _place_of
        from ...heartbeat.experience.stages import depth_of, level_of
        before = [_place_of(one) for one in self.store.stages([self.unit["id"]])
                  if one.get("card_id") == card.get("id")][-PLACE_DEPTH:]
        _root, cards = self._tree()
        text = " ".join(filter(None, [str(summary.get("objective") or ""),
                                      str(summary.get("result") or ""),
                                      card.get("title", ""), card.get("statement", "")]))
        as_stage = {"summary": text, "work": {"output": {"what": text}},
                    "medium": card.get("type", "")}
        return {"kind": str(summary.get("kind") or "execution"),
                "type": card.get("type") or "",
                "before": before,
                "level": level_of(depth_of(card, cards)) if card.get("id") in cards else "",
                "environment": [t for t in stage_terms(as_stage, card)
                                if t.startswith("env:") and not t.startswith("env:medium:")],
                "tasks": self.project_tasks, "text": text}

    def _reviewer_context(self, card: Optional[Dict[str, Any]], recent: bool = False
                          ) -> str:
        """The work in brief for the reviewer - room kept for the user's
        experience: what was asked, the contract with what each criterion
        rests on, the way to this card, the cards beside it, and its last
        attempts; and the last rounds."""
        root, cards = self._tree()
        lines = [f"THE WORK: {self.unit.get('name', '')}",
                 *filter(None, [self._description_text()]),
                 f"WHAT THE USER ASKED: {self.instruction or '(carry it forward)'}"]
        contract = self._contract_text(for_reviewer=True)
        if contract:
            lines.append(contract)
        elif self._branch_words():
            lines.append(self._words_text())
        if card is not None and card.get("id"):
            path, here = [], card
            while here is not None:
                path.append(here)
                here = cards.get(here.get("parent_id") or "")
            path.reverse()
            lines.append("THE WAY TO THIS CARD:")
            lines += [f"  {'  ' * n}[{one['id']}] {one['title']} - {one['statement'][:200]}"
                      for n, one in enumerate(path)]
            siblings = [one for one in cards.values()
                        if one.get("parent_id") == card.get("parent_id")
                        and one["id"] != card.get("id") and card.get("parent_id")]
            if siblings:
                lines.append("BESIDE IT:")
                lines += [f"  [{one['id']}] {one['status']}: {one['title']}"
                          for one in siblings[:12]]
            attempts = [one for one in self.store.stages([self.unit["id"]])
                        if one.get("card_id") == card.get("id")][-3:]
            if attempts:
                lines.append("ITS LAST ATTEMPTS:")
                lines += [f"  [{one['id']}] {one['kind']}"
                          f"{' (' + (one['evaluation'] or {}).get('verdict', '') + ')' if (one['evaluation'] or {}).get('verdict') else ''}"
                          f": {one['summary'][:300]}" for one in attempts]
        if recent and self.plan.get("rounds"):
            lines.append("THE LAST ROUNDS:")
            lines += [f"  {one['n']}. {one['card']}: {one['objective']} - {one['progress']}"
                      for one in self.plan["rounds"][-3:]]
        return "\n".join(lines)


class Review:
    """One context of the reviewer's: the work in brief and how the user
    worked on things like it - retrieved once, for this question - then the
    question, and any asked after it in the same context."""

    def __init__(self, run: Run, profile: Dict[str, Any], context: str,
                 work: bool = True, same_as: str = "") -> None:
        """`same_as`: the card, and what it is asked for ("criteria:<card>",
        "round:<card>") - THE SAME CARD IS GIVEN THE SAME past work every
        time: what it was given the first time is kept (`Run.experience_given`),
        reopened or reviewed again or carried on after the application closed,
        even while learning changes the record meanwhile."""
        self.run = run
        self.work = work
        #: A reviewer with NO CONTEXT (the Experience debugger's second way):
        #: nothing of the user's experience is retrieved for it, nor said to
        #: it - its questions are asked without what they say of it.
        self.plain = getattr(run, "variant", FULL) == NO_CONTEXT
        self.messages: List[Dict[str, Any]]
        if self.plain:
            self.messages = [{"role": "user", "content": (
                context + "\n\nNO RECORD OF THE USER'S PAST WORK is available to "
                "you: judge by what they asked, their own words, the contract and "
                "the evidence.")}]
            return
        found: Dict[str, Any] = {}
        kept = run.experience_given(same_as) if same_as else None
        if kept is not None:
            found = kept
        elif run.retriever is not None:
            asked = run._in_english(profile)
            found = compact_experience(run.retriever.find(asked))
            run.keep_experience_given(same_as, found)
        self.messages = [{"role": "user", "content": (
            context
            + "\n\nHOW THE USER WORKS, FROM THEIR OWN PAST WORK - first the RULES "
              "they have shown (principles, patterns and natures, each whole with "
              "the cases that show it), then the past STAGES they come from, a "
              "line each with why it was found; open_stage opens any of them:\n"
            + experience_text(found, EXPERIENCE_CHARS))}]

    def ask(self, content: str) -> Dict[str, Any]:
        """Asked in the same context every time: the first question joins
        what it was told; later ones follow its answers."""
        if self.plain:
            for said, instead in NO_CONTEXT_WORDS:
                content = content.replace(said, instead)
        last = self.messages[-1] if self.messages else None
        if last is not None and last["role"] == "user" and \
                isinstance(last["content"], str):
            last["content"] = last["content"] + "\n\n" + content
        else:
            self.messages.append({"role": "user", "content": content})
        system = REVIEWER_SYSTEM_NO_CONTEXT if self.plain else REVIEWER_SYSTEM
        return self.run._ask(system.replace("{checks}", str(self.run.checks_folder))
                             + "\n\n" + zone_rule() + native_rule(),
                             self.messages, self.run.review_tools, writing=False,
                             work=self.work, who="reviewer")
