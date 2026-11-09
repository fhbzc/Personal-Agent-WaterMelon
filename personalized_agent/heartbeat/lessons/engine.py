"""THE LESSONS, LEARNED FROM EVIDENCE - one system for every kind: the
notification lessons, the connection lessons, and the natures of the user's
work (`natures.py`: their stages are the evidence, the natures the rules).
Above them, for every kind alike, patterns and principles grouped by
experience's builder (`groups.py`).

WHEN: never on the user's click - an edit is written down, and the cycle
runs with the state heartbeat's round, for each kind with something new
(`due`, `learn_due`); or when the user asks (Learn now).

A lesson is a belief about how the user wants things done, backed by
evidence - never written from one event. It grows stronger, narrower or
weaker as evidence arrives, including evidence of whether acting on it
worked.

  EVIDENCE   ONLY WHAT THE USER DID THEMSELVES - added, deleted, edited,
             disabled, and EVERY ANSWER to a notification or a question (the
             user, 2026-11-03: Got it, Remind me later, Add to to-do, Open,
             Not now, Allow...: each says something different) - with their
             reason when they gave one; and among it, the user undoing what
             the agent did on a lesson (`used`, `outcome`). What the agents do
             - a decision left standing, the reviewer opening work again - is
             never evidence, nor is a notification left unanswered.
             KEPT AS LEARNING KEEPS ITS WORK (the user, 2026-11-03): each
             piece an EXAMPLE, marked with its class, its exact content kept,
             said in English; the rules above them are the possible universal
             ones. All in English, for the model, never shown to the user;
             found by one way (`retrieve.py`).
  RULES      hypotheses with explicit conditions, each with the evidence
             that supports and the evidence that contradicts it. A rule is a
             CANDIDATE until enough evidence backs it, then ACTIVE - a lesson
             the agent is given - and RETIRED or REJECTED when it is not.

THE CYCLE (`learn`), run in the background after each of the user's edits:

  1. INGEST    each new piece of evidence gets features - its source, kind
               of event, person, English keywords - in one batched call.
  2. MATCH     code shortlists, for each, up to `SHORTLIST` rules ranked by
               how much they share with it; the model then judges each pair:
               SUPPORTS (the rule's conditions hold and the user did what it
               predicts), CONTRADICTS (its conditions all hold and the user
               did the opposite), or unrelated. Each judgement is kept, with
               why. Evidence nothing explains goes to the pool. WEIGHED BOTH
               WAYS (the user, 2026-11-07): beside its shortlist, each piece
               is shown the lessons closest to the ones it was listed with -
               in the same call - so that a lesson pointing the other way is
               contradicted by it, not left standing beside the one it
               supports.
  3. INDUCE    pooled evidence that shares features, `GROUP_AT` or more
               pieces, is shown to the model together: one rule that
               explains it, with its conditions - or none.
  4. SCORE     support and contradiction, each piece weighted (an action 1;
               with a reason given 2; a correction of the agent 1.5) and
               halving every
               `HALF_LIFE_DAYS`; confidence = (S + 1) / (S + C + 2).
  5. DECIDE    a candidate becomes a lesson at S >= `PROMOTE_SUPPORT` and
               confidence >= `PROMOTE_CONFIDENCE`; a lesson below
               `REVISE_BELOW` is revised - narrowed, split or retired - from
               its supporting and contradicting evidence side by side; two
               lessons that rest on the same evidence are merged; one with no
               support for `STALE_DAYS` retires. A CANDIDATE retires only
               when contradicted more than supported (the user, 2026-11-07);
               one with no new evidence for long stays ("很久没有新证据可以留着").
               The user's pin, rejection and edits win.
  6. PUBLISH   `text`: only the lessons in force, each with its id, its
               confidence and how many cases back it - what the agent is
               given, never the raw actions.

A BAD OUTCOME BLAMES ONLY WHAT CAUSED IT (the user, 2026-11-07): the user
undoing what the agent did on lessons is kept, and in the next cycle the
model is asked, once for each, which of the lessons used caused it; the
contradiction goes to those alone. Not asked, or no whole answer: nothing
is recorded - never spread over every lesson used.

THE UPKEEP (`upkeep`, the user 2026-11-07: keeping the rules is the
system's job, not hand edits), with the round's learning (`learn_due`), for
each kind whose rules or waiting evidence changed since the last; each step
in batches, each question asked once (`_asked`):

  RETAKE      evidence linked to no rule `RETAKE_AFTER_DAYS` after it was
              read is taken up again, ONCE: linked to a rule it fits, or a
              candidate made from it (the work: linked only - its natures
              come from its stages).
  DUPLICATES  rules worded much alike, candidates and lessons together, are
              shown to the model: the same ones merge - one statement, the
              evidence and history joined, the other marked merged into it.
  OPPOSITES   rules about the same thing are shown in pairs: pointing
              opposite ways, one is narrowed, the two merged, or one retired
              - each with why, in its history.
  A rule the user pinned, stated or worded is never rewritten or retired by
  the model. None of it is evidence: only what the user did moves how sure
  a lesson is.
"""

from __future__ import annotations

from ...common.languages import in_display, in_english

import json
import logging
import math
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: The most rules a piece of evidence is judged against, the most relevant
#: first.
SHORTLIST = 20
#: Pooled evidence of one class or sharing features, this many or more, is
#: induced from: a POSSIBLE rule, a candidate until enough backs it.
GROUP_AT = 2
#: How alike two pieces of pooled evidence must be to be grouped.
GROUP_SCORE = 3.0
#: Evidence counts half as much after this many days.
HALF_LIFE_DAYS = 60.0
#: A candidate becomes a lesson at this weighted support and confidence.
PROMOTE_SUPPORT = 3.0
#: ... by kind, where one piece of evidence says more: a work stage is a whole
#: episode of the user's work, and a nature is formed from one - two, not
#: long ago, put it in force.
PROMOTE_BY_KIND = {"work": 1.5}
#: ... and the confidence that goes with it: two stages and nothing against
#: them is (1.5 + 1) / (1.5 + 2), a little over 0.7.
PROMOTE_CONFIDENCE_BY_KIND = {"work": 0.7}
PROMOTE_CONFIDENCE = 0.75
#: A lesson below this confidence is revised.
REVISE_BELOW = 0.6
#: A lesson with no support this long, and little left, retires.
STALE_DAYS = 120
#: Two lessons whose evidence overlaps this much are offered for merging.
MERGE_OVERLAP = 0.5
#: An agent's decision left uncorrected this long counts as accepted.
SETTLE_DAYS = 3

#: How much one piece of evidence weighs.
WEIGHT_ACTION = 1.0
WEIGHT_REASON = 2.0
WEIGHT_CORRECTION = 1.5

#: THE UPKEEP (the user, 2026-11-07). How alike two rules' words must be
#: (their share in common) to be shown the model as about the same thing -
#: and, beside a piece of evidence, as close to a lesson it was listed with.
ALIKE_AT = 0.3
#: ... and as worded much the same: offered for merging (on the user's lessons of
#: 2026-11-07, 8 pairs of work rules are at 0.5 or more - as many as the audit found).
DUPLICATE_AT = 0.5
#: The most close lessons added beside one piece of evidence's shortlist.
NEIGHBOURS = 5
#: Evidence linked to no rule is taken up again this long after it was read:
#: the pool's grouping (`_induce`) has had its chance first.
RETAKE_AFTER_DAYS = 7
#: The most pairs, and pieces of evidence, one upkeep takes up - the rest
#: with the next.
MOST_PAIRS = 60
MOST_RETAKE = 60
#: The most pairs, or pieces, one upkeep call is given.
UPKEEP_BATCH = 10
#: The questions already asked of the upkeep, kept this many.
MOST_ASKED = 4000

#: The most pieces of evidence one call is given.
BATCH = 30
#: What is kept of an example's exact content.
MOST_CONTENT = 12000

#: What each kind of lesson is about - said to the model.
KINDS = {
    "notifications": ("Notification lessons",
                      "what the user did to the notifications and calendar events the "
                      "agent made or they made themselves, and how they answered every "
                      "notification and question. EACH ANSWER SAYS SOMETHING DIFFERENT: "
                      "accepted, opened, allowed or added to the to-do list - it was right; "
                      "not now, later or remind me later - the MOMENT was wrong, not "
                      "necessarily the content; not today - enough of this today; denied - "
                      "the agent should not do this; a bad notification - it should never "
                      "have been said; got it - seen, with no complaint, a weak sign"),
    "connections": ("Connection lessons",
                    "what the user did to their contacts - added, edited, deleted, merged"),
    "work": ("How you work",
             "the stages of the user's own work - what they decided, required, "
             "accepted and rejected"),
    "recommendations": ("Recommendation lessons",
                        "what the user did with what was recommended to them, in the newspaper "
                        "and in the moment by the Monitor. Wanted more of it, read the detail or "
                        "kept it in their collection - it was right; good to know - fine, no "
                        "more than that; not interested or a bad notification - not this; "
                        "already knew it - right subject, nothing new in it"),
    "general": ("General lessons",
                "what the user told their agent to remember about how they want things done"),
}

#: The kinds learned with the state heartbeat's round.
LEARNED_KINDS = ("notifications", "recommendations", "connections", "work", "general")
#: Each kind's module (common/modules.py): one turned off is not learned.
KIND_MODULE = {"notifications": "calendar", "connections": "connections",
               "recommendations": "recommend",
               "work": "experience"}

FEATURES_PROMPT = """You read records of {about}, and give each one the features it is
matched by. Return ONLY this JSON object:
{{"features": {{"<record id>": {{"source": "where it came from - an app or channel, e.g. wechat, email, screen, calendar, manual",
                                "event_type": "what kind of thing it is about, in two or three words, e.g. meeting, deadline, payment, person",
                                "person": "the person it is about or with, if any, as named",
                                "keywords": ["3 to 8 words that say what it is about"],
                                "category": "its class: two to four words naming the kind of case, reusing one of the classes below when it fits",
                                "english": "one or two sentences: what the situation was and what the user did or answered - in general terms, by which a like moment finds it"}}}}}}

Lower case, but for "english".

THE CLASSES THERE ARE SO FAR: {categories}

THE RECORDS:
{records}"""

JUDGE_PROMPT = """You keep the lessons on how one person wants things done - from {about}.
Below, each NEW RECORD of something the user did comes with the LESSONS that
might bear on it, the most relevant first. For each pair, judge:

  - "supports": the lesson's conditions hold for this record, and the user
    did what the lesson predicts.
  - "contradicts": the lesson's conditions ALL hold for this record, and the
    user did the opposite.
  - "unrelated": the lesson's conditions do not all hold. A case outside a
    lesson's scope never counts against it.

WEIGH IT BOTH WAYS: a lesson marked "close to" another is about much the
same thing. When a record supports one lesson, look at the others listed
with it: one whose conditions hold but which says the opposite of what the
user did is CONTRADICTED by it.

Return ONLY this JSON object, listing only pairs that support or contradict:
{{"judgements": [{{"record": "<record id>", "lesson": "<lesson id>",
                  "relation": "supports|contradicts", "why": "one sentence"}}]}}

{pairs}"""

INDUCE_PROMPT = """You keep the lessons on how one person wants things done - from {about}.
These records of what the user did share something. Is there ONE rule that
explains them - when this, the user wants that? State it with its
conditions, as narrow as the records need. If they do not share a rule,
say so.

Return ONLY this JSON object:
{{"rule": {{"statement": "the rule, one sentence, said to the agent: when ..., do / do not ...",
           "conditions": {{"source": "...", "event_type": "...", "person": "...",
                          "action": "...", "keywords": ["..."], "other": "any other condition"}}}},
 "explains": ["the ids of the records it explains"]}}
or {{"rule": null}} when there is none.

THE RECORDS:
{records}"""

REVISE_PROMPT = """You keep the lessons on how one person wants things done - from {about}.
This lesson is being contradicted. Below are the records that SUPPORT it and
the records that CONTRADICT it. Decide:

  - "narrow": one condition separates them - the lesson holds, but only when
    it is met. Give the narrowed lesson.
  - "split": they are two different rules. Give both.
  - "retire": no condition explains the difference; it no longer holds.

Return ONLY this JSON object:
{{"decision": "narrow|split|retire",
 "rules": [{{"statement": "...", "conditions": {{"source": "...", "event_type": "...",
             "person": "...", "action": "...", "keywords": ["..."], "other": "..."}}}}],
 "why": "one sentence"}}
THE LESSON: {statement}
ITS CONDITIONS: {conditions}

SUPPORTING:
{supporting}

CONTRADICTING:
{contradicting}"""

MERGE_PROMPT = """Two lessons on how one person wants things done rest on much of the same
evidence. Do they say the same thing?

A: {a}
B: {b}

Return ONLY this JSON object:
{{"same": true, "statement": "the one lesson that says both"}}
or {{"same": false}}."""

BLAME_PROMPT = """You keep the lessons on how one person wants things done - from {about}.
The agent acted on the lessons below, and the user then undid or corrected
what it did. Which of these lessons CAUSED the part the user corrected? A
lesson that was followed but did not lead to that part did not cause it -
it may be that none of them did.

WHAT THE AGENT DID: {decision} ({subject})
WHAT THE USER DID: {summary}

THE LESSONS IT FOLLOWED:
{lessons}

Return ONLY this JSON object:
{{"caused": ["the id of each lesson that caused it - none, one or more"], "why": "one sentence"}}"""

RETAKE_PROMPT = """You keep the lessons on how one person wants things done - from {about}.
Each RECORD below is something the user did that no lesson explains yet,
with the lessons closest to it, if any. For each record, decide one:

  - "fits": a listed lesson's conditions all hold for it - it "supports" the
    lesson (the user did what it says) or "contradicts" it (the opposite).
{new}  - "none": {none}

Return ONLY this JSON object, with an entry for EVERY record:
{{"records": [{{"record": "<record id>", "decision": "fits|{new_word}none",
               "lesson": "<the lesson's id, for fits>", "relation": "supports|contradicts",
               "rule": {{"statement": "for new: the rule, one sentence, said to the agent: when ..., do / do not ...",
                        "conditions": {{"source": "...", "event_type": "...", "person": "...",
                                       "action": "...", "keywords": ["..."], "other": "..."}}}},
               "why": "one sentence"}}]}}

{records}"""

DUPLICATES_PROMPT = """You keep the lessons on how one person wants things done - from {about}.
Each PAIR below is two lessons worded much alike. Do they say the SAME
thing - the same case, the same wish - so that one lesson would do for both?

Return ONLY this JSON object, with an entry for EVERY pair:
{{"pairs": [{{"pair": "<pair id>", "same": true,
             "statement": "for the same: the one lesson that says both", "why": "one sentence"}}]}}
("same": false when they differ.)

{pairs}"""

OPPOSITES_PROMPT = """You keep the lessons on how one person wants things done - from {about}.
Each PAIR below is two lessons about much the same thing. Do they point
OPPOSITE ways - one says do, the other do not, for the same case? For each
pair, decide one:

  - "narrow": both hold, each in its own cases. Rewrite ONE of them (its id)
    with the condition that separates them.
  - "merge": they are one lesson after all. Give the one statement.
  - "retire": one of them is wrong - give its id; the one its cases back
    less.
  - "none": they do not point opposite ways for the same case. Leave both.

A lesson marked SET BY THE USER is never rewritten or retired.

Return ONLY this JSON object, with an entry for EVERY pair:
{{"pairs": [{{"pair": "<pair id>", "decision": "narrow|merge|retire|none",
             "lesson": "<the id narrowed or retired>",
             "statement": "for narrow or merge: the new statement",
             "conditions": {{"source": "...", "event_type": "...", "person": "...",
                            "action": "...", "keywords": ["..."], "other": "..."}},
             "why": "one sentence"}}]}}

{pairs}"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime] = None) -> str:
    return (value or _now()).isoformat()


def _when(value: Any) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _loads(value: Any, default: Any) -> Any:
    try:
        said = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default
    return said if isinstance(said, type(default)) else default


def _json_of(text: str) -> Dict[str, Any]:
    text = str(text or "")
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        return {}
    try:
        said = json.loads(found.group(0))
    except ValueError:
        return {}
    return said if isinstance(said, dict) else {}


def _words(values: Iterable[Any]) -> set:
    out = set()
    for one in values or []:
        for word in re.findall(r"[a-z0-9]+", str(one).lower()):
            if len(word) > 2:
                out.add(word)
    return out


def _same(a: Any, b: Any) -> bool:
    a, b = " ".join(str(a or "").lower().split()), " ".join(str(b or "").lower().split())
    return bool(a) and a == b


def relevance(features: Dict[str, Any], conditions: Dict[str, Any],
              statement: str = "") -> float:
    """How much a piece of evidence shares with a rule's conditions: a
    shared person counts most, then the source and the kind of event, the
    action, and the share of keywords in common."""
    score = 0.0
    if _same(features.get("person"), conditions.get("person")):
        score += 3.0
    if _same(features.get("source"), conditions.get("source")):
        score += 2.0
    if _same(features.get("event_type"), conditions.get("event_type")):
        score += 2.0
    if _same(features.get("action"), conditions.get("action")):
        score += 1.0
    mine = _words(features.get("keywords") or [])
    theirs = _words(list(conditions.get("keywords") or []) + [statement,
                                                              conditions.get("other") or ""])
    if mine and theirs:
        score += 4.0 * len(mine & theirs) / len(mine | theirs)
    return round(score, 4)


#: Words that say nothing of what a rule is about. "not", "never" stay: they
#: are what makes two rules point opposite ways.
_FILLER = set("the and for that with this from they their them user users when what which "
              "into about have has are was were will would should but also its each every "
              "more most than then agent any all can one own".split())


def rule_words(rule: Dict[str, Any]) -> set:
    """What a rule is about, as words: its statement and its keywords, a
    plural taken as its singular."""
    conditions = rule.get("conditions") if isinstance(rule.get("conditions"), dict) else {}
    out = set()
    for word in _words([rule.get("statement") or ""] + list(conditions.get("keywords") or [])):
        if word in _FILLER:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.add(word)
    return out


def alike(a: set, b: set) -> float:
    """How alike two rules are (their `rule_words`): the share in common."""
    return round(len(a & b) / len(a | b), 4) if a and b else 0.0


class LessonEngine:
    """Every kind of lesson, learned the same way (module docstring)."""

    def __init__(self, db: Any, models: Any = None, config: Any = None,
                 language: Optional[Callable[[], str]] = None) -> None:
        self.db = db
        self.models = models
        self.config = config
        self._language = language
        #: THE NATURES OF THE USER'S WORK, kept as rules here (natures.py) -
        #: set by the application: brought in before a work cycle, and what
        #: was learned written back onto them after.
        self.natures: Any = None
        #: Patterns and principles for a kind of lesson, grouped as natures
        #: are (groups.py) - set by the application: kind -> report.
        self.grouper: Optional[Callable[[str], Dict[str, Any]]] = None
        self._lock = threading.RLock()
        self._worker: Optional[threading.Thread] = None
        self._queued: set = set()
        #: WHERE EACH KIND'S CYCLE IS, for the state heartbeat's bars: the
        #: step, how far through it, when it last finished.
        self.progress: Dict[str, Dict[str, Any]] = {}

    # -- what the user did ---------------------------------------------------
    #: What the user is doing now, for a lesson made now (set by the application).
    moment_now: Any = None

    def record(self, kind: str, source_id: str, summary: str, action: str = "",
               reason: str = "", at: str = "", weight: Optional[float] = None,
               outcome_of: str = "", learn: bool = False, content: str = "",
               moment: Optional[Dict[str, Any]] = None) -> str:
        """One piece of evidence, kept - learned from with the next round
        (or at once, with `learn`). `content`: exactly what it was - what was
        shown, why it was made - kept with it. `moment`: what the user was doing when it was
        shown (lessons/moment.py); else now, unless this is from another time, or the work's."""
        from ...common.events.models import new_id
        from .moment import line_of
        if moment is None and not at and kind != "work" and self.moment_now is not None:
            try:
                moment = self.moment_now()
            except Exception:
                log.debug("what the user was doing could not be read", exc_info=True)
        said_of_it = line_of(moment)
        if said_of_it:
            summary = f"{str(summary or '')[:3800]} {said_of_it}"
        reason = " ".join(str(reason or "").split())[:1000]
        if weight is None:
            weight = WEIGHT_REASON if reason else WEIGHT_ACTION
        ident = new_id("evi")
        self.db.execute(
            "INSERT INTO lesson_evidence (id, kind, source_id, at, action, summary, reason, "
            "weight, features, processed_at, outcome_of, content, moment) VALUES (?, ?, ?, ?, ?, "
            "?, ?, ?, NULL, NULL, ?, ?, ?)",
            (ident, kind, str(source_id or ""), at or _iso(), str(action or "")[:40],
             str(summary or "")[:4000], reason, float(weight), str(outcome_of or ""),
             str(content or "")[:MOST_CONTENT], json.dumps(moment or {}, ensure_ascii=False)))
        if learn:
            self.learn_in_background(kind)
        return ident

    def the_user_did(self, action: str, said: str, source_id: str = "") -> str:
        """WHAT THE USER DID TO HOW THEIR WORK IS NAMED OR GROUPED (the user,
        2026-11-04): a task or project named or deleted, a list's groups moved -
        an example like any other, found by the retrieval of the next run that
        names or groups anything."""
        return self.record("general", source_id, said, action=action)

    def rule_from_the_user(self, kind: str, statement: str, evidence: str) -> str:
        """A rule the user stated themselves, in the chat: in force at once,
        its example the conversation it was said in."""
        rule = self._new_rule(kind, {"statement": statement}, "stated by the user in the chat",
                              state="active")
        self.db.execute("INSERT OR REPLACE INTO lesson_links (evidence_id, rule_id, relation, "
                        "why, at) VALUES (?, ?, 'supports', ?, ?)",
                        (evidence, rule, "the user stated it", _iso()))
        return rule

    def rebuild_once(self) -> int:
        """ONCE (the user, 2026-11-03: "所有lessons都要重新弄"): the notification
        and connection lessons made again as examples and rules in English -
        their rules let go (one the user pinned or rejected stays), every
        piece of evidence read afresh with the next round. The natures of the
        work are learning's own, and stay."""
        key = "lessons.rebuilt.examples"
        done = self.db.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        if done and done["value"]:
            return 0
        made = 0
        for kind in ("notifications", "connections"):
            rules = self.db.query("SELECT id FROM lesson_rules WHERE kind = ? AND pinned = 0 "
                                  "AND state != 'rejected'", (kind,))
            for row in rules:
                self.db.execute("DELETE FROM lesson_links WHERE rule_id = ?", (row["id"],))
                self.db.execute("DELETE FROM lesson_rules WHERE id = ?", (row["id"],))
            cursor = self.db.execute("UPDATE lesson_evidence SET features = NULL, "
                                     "processed_at = NULL WHERE kind = ?", (kind,))
            made += int(cursor.rowcount or 0)
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                        "SET value = excluded.value", (key, _iso()))
        return made

    def forget_source(self, kind: str, source_id: str) -> int:
        """The evidence of one action taken back - and what it said of every
        rule with it."""
        rows = self.db.query("SELECT id FROM lesson_evidence WHERE kind = ? AND source_id = ?",
                             (kind, str(source_id)))
        with self._lock:
            for row in rows:
                self.db.execute("DELETE FROM lesson_links WHERE evidence_id = ?", (row["id"],))
                self.db.execute("DELETE FROM lesson_evidence WHERE id = ?", (row["id"],))
            if rows:
                self.score(kind)
        return len(rows)

    # -- the agent acting on a lesson ------------------------------------------------
    def used(self, kind: str, rule_ids: Sequence[str], subject_id: str,
             decision: str) -> Optional[str]:
        """The agent acted on these lessons, about this subject: kept, so the
        user's next move on it says whether the lessons were right."""
        from ...common.events.models import new_id
        known = {row["id"] for row in self.rules(kind, states=("active", "candidate"))}
        ids = [str(one) for one in rule_ids or [] if str(one) in known]
        if not ids or not subject_id:
            return None
        ident = new_id("use")
        self.db.execute("INSERT INTO lesson_uses (id, kind, rule_ids, subject_id, decision, at, "
                        "resolved_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                        (ident, kind, json.dumps(ids), str(subject_id), str(decision), _iso()))
        return ident

    #: A bad outcome waiting to be judged: kv key prefix, then the use's id.
    BLAME_KEY = "lessons.blame."

    def outcome(self, kind: str, subject_id: str, summary: str) -> int:
        """The user undid what the agent did on a lesson - deleted, disabled
        or changed it: a correction, kept for the next cycle to judge which
        of the lessons it followed caused it (`_blame`). How many decisions
        it corrects."""
        rows = self.db.query("SELECT * FROM lesson_uses WHERE kind = ? AND subject_id = ? "
                             "AND resolved_at IS NULL", (kind, str(subject_id)))
        for use in rows:
            # NOT ON THE USER'S CLICK, AND NOT ON EVERY LESSON USED (the user, 2026-11-07: a
            # sound rule was blamed for a choice another rule, or none, caused): kept, and the
            # model asked in the cycle which of them caused it.
            self._kv_set(self.BLAME_KEY + use["id"], json.dumps(
                {"kind": kind, "summary": str(summary or "")[:1200], "at": _iso()},
                ensure_ascii=False))
            self.db.execute("UPDATE lesson_uses SET resolved_at = ? WHERE id = ?",
                            (_iso(), use["id"]))
        return len(rows)

    def _blames_waiting(self, kind: str) -> List[Tuple[str, Dict[str, Any]]]:
        out = []
        for row in self.db.query("SELECT key, value FROM kv WHERE key LIKE ?",
                                 (self.BLAME_KEY + "%",)):
            said = _loads(row["value"], {})
            if said.get("kind") == kind:
                out.append((str(row["key"])[len(self.BLAME_KEY):], said))
        return out

    def _blame(self, kind: str) -> Dict[str, Any]:
        """Each bad outcome waiting: the model asked, once, which of the
        lessons used caused it - the contradiction to those alone. Not asked,
        or its answer not whole: nothing recorded, and not asked again."""
        about = KINDS.get(kind, (kind, kind))[1]
        done: Dict[str, Any] = {"judged": 0, "blamed": 0, "failed": []}
        for use_id, said in self._blames_waiting(kind):
            use = self.db.query_one("SELECT * FROM lesson_uses WHERE id = ?", (use_id,))
            rules = [self.rule(one) for one in _loads((use or {}).get("rule_ids"), [])]
            rules = [one for one in rules if one is not None]
            self.db.execute("DELETE FROM kv WHERE key = ?", (self.BLAME_KEY + use_id,))
            if not rules:
                continue
            read = self._read(BLAME_PROMPT.format(
                about=about, decision=str(use.get("decision") or ""),
                subject=str(use.get("subject_id") or ""), summary=said.get("summary") or "",
                lessons="\n".join(f"[{one['id']}] {one['statement']}" for one in rules)),
                language=in_english("why"), wants=("caused",))
            caused = (read.data or {}).get("caused") if read.ok else None
            if not isinstance(caused, list):
                # NEVER SPREAD (the user, 2026-11-07): no whole answer, nothing recorded.
                why = read.why_not or "the answer named no list of lessons"
                log.info("the lessons could not judge a correction (%s): %s", use_id, why)
                done["failed"].append({"use": use_id, "why": why})
                continue
            done["judged"] += 1
            used = {one["id"] for one in rules}
            why = " ".join(str((read.data or {}).get("why") or "").split())[:300]
            for rule_id in sorted({str(one) for one in caused} & used):
                self._linked_evidence(kind, rule_id, "contradicts", WEIGHT_CORRECTION,
                                      f"The user corrected what the agent did on this "
                                      f"lesson: {said.get('summary') or ''}", outcome_of=use_id,
                                      at=str(said.get("at") or ""),
                                      why=f"the outcome of the agent acting on it - it caused "
                                          f"it: {why}" if why else "")
                done["blamed"] += 1
        return done

    def _kv_set(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                        "SET value = excluded.value", (key, value))

    def settle_uses(self, kind: str) -> int:
        """Decisions on lessons the user left alone `SETTLE_DAYS`: no longer
        waited on. NOT EVIDENCE - leaving something alone is not something
        the user did, and only what they do moves how sure a lesson is."""
        until = _iso(_now() - timedelta(days=SETTLE_DAYS))
        rows = self.db.query("SELECT id FROM lesson_uses WHERE kind = ? AND resolved_at IS NULL "
                             "AND at <= ?", (kind, until))
        for use in rows:
            self.db.execute("UPDATE lesson_uses SET resolved_at = ? WHERE id = ?",
                            (_iso(), use["id"]))
        return len(rows)

    def forget_the_agents_own(self) -> int:
        """Once: evidence that was the agents' doing and not the user's - a
        decision left standing, work the reviewer opened again - taken out,
        and how sure each lesson is worked out again without it."""
        rows = self.db.query(
            "SELECT e.id, e.kind FROM lesson_evidence e WHERE e.action = 'outcome' AND "
            "(e.kind = 'work' OR e.summary LIKE 'What the agent did on this lesson%')")
        for row in rows:
            self.db.execute("DELETE FROM lesson_links WHERE evidence_id = ?", (row["id"],))
            self.db.execute("DELETE FROM lesson_evidence WHERE id = ?", (row["id"],))
        self.db.execute("DELETE FROM lesson_uses WHERE kind = 'work'")
        for kind in {row["kind"] for row in rows}:
            self.score(kind)
        return len(rows)

    def _linked_evidence(self, kind: str, rule_id: str, relation: str, weight: float,
                         summary: str, outcome_of: str, at: str = "", why: str = "") -> None:
        ident = self.record(kind, outcome_of, summary, action="outcome", weight=weight,
                            outcome_of=outcome_of, learn=False, at=at)
        self.db.execute("UPDATE lesson_evidence SET processed_at = ?, features = '{}' "
                        "WHERE id = ?", (_iso(), ident))
        self.db.execute("INSERT OR REPLACE INTO lesson_links (evidence_id, rule_id, relation, "
                        "why, at) VALUES (?, ?, ?, ?, ?)",
                        (ident, rule_id, relation,
                         (why or "the outcome of the agent acting on it")[:400], _iso()))

    # -- the user's own say over the lessons ----------------------------------------------------
    def set_state(self, rule_id: str, op: str, statement: str = "") -> Optional[Dict[str, Any]]:
        """confirm (a lesson now) | reject (never again) | pin | unpin |
        edit (its words)."""
        rule = self.rule(rule_id)
        if rule is None:
            return None
        fields: Dict[str, Any] = {}
        if op == "confirm":
            fields = {"state": "active"}
        elif op == "reject":
            fields = {"state": "rejected", "pinned": 0}
        elif op == "pin":
            fields = {"pinned": 1, "state": "active"}
        elif op == "unpin":
            fields = {"pinned": 0}
        elif op == "edit" and " ".join(str(statement).split()):
            fields = {"statement": " ".join(str(statement).split())[:600]}
        else:
            raise ValueError("confirm, reject, pin, unpin or edit")
        self._change(rule, fields, f"the user: {op}")
        return self.rule(rule_id)

    # -- reading ----------------------------------------------------------------------------
    def rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM lesson_rules WHERE id = ?", (rule_id,))
        return self._shaped(row) if row else None

    def rules(self, kind: str, states: Sequence[str] = ("candidate", "active")
              ) -> List[Dict[str, Any]]:
        marks = ", ".join("?" for _ in states)
        return [self._shaped(row) for row in self.db.query(
            f"SELECT * FROM lesson_rules WHERE kind = ? AND state IN ({marks}) "
            "ORDER BY confidence DESC, support DESC, created_at", (kind, *states))]

    @staticmethod
    def _shaped(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        out["conditions"] = _loads(row.get("conditions"), {})
        out["history"] = _loads(row.get("history"), [])
        out["pinned"] = bool(row.get("pinned"))
        return out

    def text(self, kind: str) -> str:
        """THE LESSONS IN FORCE, as the agent is given them: each with its
        id, how sure, and how many cases back it - follow the sure ones,
        lean on the rest."""
        lines = []
        for rule in self.rules(kind, states=("active",)):
            cases = self._cases(rule["id"])
            lines.append(f"- [{rule['id']}] {rule['statement']} "
                         f"({'follow' if rule['confidence'] >= 0.8 or rule['pinned'] else 'lean on it'}; "
                         f"{round(rule['confidence'] * 100)}% sure, {cases} case"
                         f"{'' if cases == 1 else 's'}"
                         f"{', set by the user' if rule['pinned'] else ''})")
        return "\n".join(lines)

    def _cases(self, rule_id: str) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM lesson_links WHERE rule_id = ? "
                                "AND relation = 'supports'", (rule_id,))
        return int((row or {}).get("n") or 0)

    def describe(self, kind: str) -> Dict[str, Any]:
        """For the Lessons panel: the lessons in force, those forming, those
        retired - each with its evidence - and how each piece of evidence
        was read."""
        def with_evidence(rule: Dict[str, Any]) -> Dict[str, Any]:
            links = self.db.query(
                "SELECT l.relation, l.why, e.id, e.summary, e.reason, e.at, e.source_id, "
                "e.action, e.category, e.english FROM lesson_links l JOIN lesson_evidence e "
                "ON e.id = l.evidence_id "
                "WHERE l.rule_id = ? ORDER BY e.at DESC", (rule["id"],))
            return dict(rule, evidence=[dict(one) for one in links][:50],
                        cases=sum(1 for one in links if one["relation"] == "supports"),
                        against=sum(1 for one in links if one["relation"] == "contradicts"),
                        to_promote=max(0.0, round(PROMOTE_SUPPORT - rule["support"], 2)))
        waiting = self.db.query_one("SELECT COUNT(*) AS n FROM lesson_evidence WHERE kind = ? "
                                    "AND processed_at IS NULL", (kind,))
        return {"groups": self.groups(kind),
                "lessons": [with_evidence(one) for one in self.rules(kind, ("active",))],
                "forming": [with_evidence(one) for one in self.rules(kind, ("candidate",))],
                "retired": [with_evidence(one) for one in self.rules(kind, ("retired",))][:20],
                "waiting": int((waiting or {}).get("n") or 0),
                "read": self.reading(kind)}

    def reading(self, kind: str) -> Dict[str, List[Dict[str, Any]]]:
        """For each action (by its source id): how its evidence was read -
        which lessons it supports or contradicts; [] when nothing explains
        it yet."""
        out: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.db.query(
                "SELECT e.source_id, e.processed_at, l.relation, l.why, r.id AS rule_id, "
                "r.statement, r.state FROM lesson_evidence e LEFT JOIN lesson_links l "
                "ON l.evidence_id = e.id LEFT JOIN lesson_rules r ON r.id = l.rule_id "
                "WHERE e.kind = ? AND e.action != 'outcome'", (kind,)):
            said = out.setdefault(row["source_id"], [])
            if row["processed_at"] is None:
                said.append({"waiting": True})
            elif row["rule_id"]:
                said.append({"relation": row["relation"], "why": row["why"],
                             "rule": row["rule_id"], "statement": row["statement"],
                             "state": row["state"]})
        return out

    # -- the cycle ------------------------------------------------------------------------------
    def learn_in_background(self, kind: str) -> None:
        with self._lock:
            self._queued.add(kind)
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._work, name="lessons", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._queued:
                    self._worker = None
                    return
                kind = self._queued.pop()
            try:
                self.learn(kind)
            except Exception:
                log.warning("learning the %s lessons failed", kind, exc_info=True)

    def wait(self, timeout: float = 60.0) -> None:
        """For tests: until the background learning has finished."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)

    def due(self) -> List[str]:
        """The kinds with something new to learn from: the user's edits not
        read yet - or, for the work, natures and stages not brought in yet."""
        out = []
        from ...common.modules import enabled as module_on
        for kind in LEARNED_KINDS:
            if not module_on(self.config, KIND_MODULE.get(kind, "core")):
                continue
            waiting = self.db.query_one(
                "SELECT COUNT(*) AS n FROM lesson_evidence WHERE kind = ? AND "
                "processed_at IS NULL", (kind,))
            fresh = kind == "work" and self.natures is not None and self.natures.due(self)
            if (waiting or {}).get("n") or fresh or self._blames_waiting(kind):
                out.append(kind)
        return out

    #: A cycle's steps, in order - its bar moves through them.
    STEPS = ("bringing in the natures", "judging the corrections", "reading the new edits",
             "matching them to the lessons", "finding new lessons", "deciding",
             "writing back onto the natures", "keeping the lessons in order", "grouping")

    def _at(self, kind: str, step: str, done: int = 0, of: int = 0) -> None:
        mine = self.progress.setdefault(kind, {})
        index = self.STEPS.index(step) if step in self.STEPS else 0
        steps = [one for one in self.STEPS
                 if kind == "work" or one not in ("bringing in the natures",
                                                  "writing back onto the natures")]
        if kind == "work":
            steps = [one for one in steps if one not in ("finding new lessons", "grouping")]
        place = steps.index(step) if step in steps else index
        inside = (done / of) if of else 0.0
        mine.update({"running": True, "step": step, "done": done, "of": of,
                     "share": round(100 * (place + inside) / max(1, len(steps)))})

    def _finished(self, kind: str, failed: str = "") -> None:
        mine = self.progress.setdefault(kind, {})
        mine.update({"running": False, "step": "", "done": 0, "of": 0, "share": 100,
                     "finished_at": _iso(), "failed": failed})

    def standing(self) -> Dict[str, Dict[str, Any]]:
        """Each kind: where its cycle is if one runs, what waits for the
        next, how many lessons are in force, when it last finished."""
        out: Dict[str, Dict[str, Any]] = {}
        due = set()
        try:
            due = set(self.due())
        except Exception:                      # pragma: no cover - a look
            pass
        for kind in LEARNED_KINDS:
            waiting = self.db.query_one(
                "SELECT COUNT(*) AS n FROM lesson_evidence WHERE kind = ? AND "
                "processed_at IS NULL", (kind,)) or {}
            counts = {row["state"]: row["n"] for row in self.db.query(
                "SELECT state, COUNT(*) AS n FROM lesson_rules WHERE kind = ? GROUP BY state",
                (kind,))}
            out[kind] = {"running": False, "share": 0 if kind in due else 100,
                         **dict(self.progress.get(kind) or {}),
                         "due": kind in due, "waiting": int(waiting.get("n") or 0),
                         "active": int(counts.get("active") or 0),
                         "forming": int(counts.get("candidate") or 0)}
            if not out[kind]["running"]:
                out[kind]["share"] = 0 if kind in due else 100
        return out

    def learn_due(self) -> Dict[str, Any]:
        """THE ROUND'S LEARNING: each kind with something new, its cycle -
        then, for a kind of lesson whose lessons changed, its patterns and
        principles grouped again."""
        said: Dict[str, Any] = {}
        for kind in self.due():
            try:
                said[kind] = self.learn(kind)
            except Exception as exc:
                log.warning("learning the %s lessons failed", kind, exc_info=True)
                said[kind] = {"failed": str(exc)[:200]}
                self._finished(kind, str(exc)[:200])
        # THE UPKEEP (the user, 2026-11-07: keeping the rules is the system's job, not hand
        # edits): each kind whose rules or waiting evidence changed since the last one.
        from ...common.modules import enabled as module_on
        for kind in LEARNED_KINDS:
            if not module_on(self.config, KIND_MODULE.get(kind, "core")) or \
                    not self._upkeep_due(kind):
                continue
            try:
                kept = self.upkeep(kind)
                if any(kept.get(key) for key in ("retaken", "merged", "opposed", "failed",
                                                 "retired_candidates", "promoted")):
                    said.setdefault(kind, {})["upkeep"] = kept
            except Exception as exc:
                log.warning("keeping the %s lessons failed", kind, exc_info=True)
                self._finished(kind, str(exc)[:200])
        for kind in ("notifications", "connections"):
            if self.grouper is None or not self._regroup_due(kind):
                continue
            self._at(kind, "grouping")
            try:
                said.setdefault(kind, {})["grouped"] = self.grouper(kind)
                self._grouped(kind)
                self._finished(kind)
            except Exception as exc:
                log.warning("grouping the %s lessons failed", kind, exc_info=True)
                self._finished(kind, str(exc)[:200])
        return said

    def _signature(self, kind: str) -> str:
        return json.dumps(sorted((rule["id"], rule["statement"])
                                 for rule in self.rules(kind, ("active",))))

    def _regroup_due(self, kind: str) -> bool:
        if self.config is None:
            return True
        return str(self.config.get(f"lessons_grouped_{kind}", "") or "") != \
            self._signature(kind)

    def _grouped(self, kind: str) -> None:
        if self.config is not None:
            self.config.set(f"lessons_grouped_{kind}", self._signature(kind), internal=True)

    def learning_now(self) -> bool:
        """Whether a learning the user asked for is going."""
        return self._worker is not None and self._worker.is_alive()

    def learn_now(self) -> None:
        """The user asked: everything due learned, off the request."""
        def run() -> None:
            try:
                self.learn_due()
            except Exception:
                log.warning("learning now failed", exc_info=True)
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=run, name="lessons-now", daemon=True)
            self._worker.start()

    def groups(self, kind: str) -> Dict[str, List[Dict[str, Any]]]:
        """The patterns and principles over a kind's lessons, as experience
        keeps them, each with the statements under it."""
        try:
            from ..experience.store import ExperienceStore
            store = ExperienceStore(self.db)
            statements = {rule["id"]: rule["statement"] for rule in self.rules(kind, ("active",))}
            patterns = store.patterns("pattern", kind=kind)
            names = {one["id"]: one.get("statement") or "" for one in patterns}
            return {"patterns": [{"id": one["id"], "statement": one.get("statement") or "",
                                  "detail": one.get("detail") or "",
                                  "lessons": [statements[m] for m in one["members"]
                                              if m in statements]}
                                 for one in patterns if one.get("statement")],
                    "principles": [{"id": one["id"], "statement": one.get("statement") or "",
                                    "detail": one.get("detail") or "",
                                    "patterns": [names[m] for m in one["members"] if m in names]}
                                   for one in store.patterns("principle", kind=kind)
                                   if one.get("statement")]}
        except Exception:                      # pragma: no cover - nothing grouped yet
            return {"patterns": [], "principles": []}

    def learn(self, kind: str) -> Dict[str, Any]:
        """One cycle over everything new (module docstring)."""
        with self._lock:
            self._learning = kind
            report: Dict[str, Any] = {"kind": kind}
            if kind == "work" and self.natures is not None:
                # The natures and their stages, brought in as rules and evidence.
                self._at(kind, "bringing in the natures")
                report["synced"] = self.natures.sync(self)
            self.settle_uses(kind)
            if self._blames_waiting(kind):
                self._at(kind, "judging the corrections")
                report["blame"] = self._blame(kind)
            fresh = [dict(row) for row in self.db.query(
                "SELECT * FROM lesson_evidence WHERE kind = ? AND processed_at IS NULL "
                "ORDER BY at, rowid", (kind,))]
            if fresh:
                self._at(kind, "reading the new edits", 0, len(fresh))
                self._ingest(kind, fresh)
                fresh = [dict(row) for row in self.db.query(
                    "SELECT * FROM lesson_evidence WHERE kind = ? AND processed_at IS NULL "
                    "AND features IS NOT NULL ORDER BY at, rowid", (kind,))]
                report["matched"] = self._match(kind, fresh)
            # A work stage's natures are formed from it when it is written
            # (experience/stage_natures.py): nothing to induce here.
            if kind != "work":
                self._at(kind, "finding new lessons")
            report["induced"] = 0 if kind == "work" else self._induce(kind)
            self.score(kind)
            self._at(kind, "deciding")
            report["decided"] = self._decide(kind)
            if kind == "work" and self.natures is not None:
                self._at(kind, "writing back onto the natures")
                report["written_back"] = self.natures.write_back(self)
            self._finished(kind)
            return report

    # 1. INGEST
    def _ingest(self, kind: str, rows: List[Dict[str, Any]]) -> None:
        about = KINDS.get(kind, (kind, kind))[1]
        from ...models import answers
        wanting = [row for row in rows if row.get("features") is None]

        def ask(part: Sequence[Dict[str, Any]]) -> Any:
            records = "\n".join(f"[{row['id']}] ({row['action']}) {row['summary'][:900]}"
                                + (f" - the user's reason: {row['reason']}" if row["reason"]
                                   else "") for row in part)
            classes = [row["category"] for row in self.db.query(
                "SELECT DISTINCT category FROM lesson_evidence WHERE kind = ? AND category != '' "
                "LIMIT 60", (kind,))]
            return self._read(FEATURES_PROMPT.format(about=about, records=records,
                                                     categories=", ".join(classes) or "none yet"),
                              language=in_english("the features, the classes and keywords"),
                              wants=("features",))
        for start in range(0, len(wanting), BATCH):
            self._at(kind, "reading the new edits", start, len(wanting))
            for part, read in answers.in_parts(wanting[start:start + BATCH], ask):
                # ONLY A WHOLE ANSWER IS TAKEN (2026-11-07): one cut off is asked again in halves;
                # a part answered and still not whole at its smallest is recorded as failed - its
                # features say so - and moved past, not asked again the same way. The model not
                # to be asked: tried again with the next edit.
                if not read.ok and read.response is None:
                    return
                if not read.ok:
                    log.info("the lessons failed to read %d record(s): %s", len(part),
                             read.why_not)
                    for row in part:
                        self.db.execute(
                            "UPDATE lesson_evidence SET features = ? WHERE id = ?",
                            (json.dumps({"action": row["action"], "keywords": [],
                                         "failed": read.why_not}, ensure_ascii=False),
                             row["id"]))
                    continue
                self._features_of(part, read.data or {})

    def _features_of(self, part: Sequence[Dict[str, Any]], said: Dict[str, Any]) -> None:
        """What a whole answer said of each record, kept."""
        got = said.get("features") if isinstance(said.get("features"), dict) else {}
        for row in part:
            one = got.get(row["id"]) if isinstance(got.get(row["id"]), dict) else {}
            features = {"source": str(one.get("source") or "")[:60].lower(),
                        "event_type": str(one.get("event_type") or "")[:60].lower(),
                        "person": str(one.get("person") or "")[:80],
                        "keywords": [str(k).lower()[:40] for k in one.get("keywords") or []][:10],
                        "action": row["action"]}
            self.db.execute(
                "UPDATE lesson_evidence SET features = ?, category = ?, english = ? WHERE id = ?",
                (json.dumps(features, ensure_ascii=False),
                 " ".join(str(one.get("category") or "").lower().split())[:60],
                 " ".join(str(one.get("english") or "").split())[:600], row["id"]))

    # 2. MATCH
    def shortlist(self, features: Dict[str, Any], rules: List[Dict[str, Any]]
                  ) -> List[Tuple[float, Dict[str, Any]]]:
        """The rules a piece of evidence is judged against: up to `SHORTLIST`,
        the most relevant first - only those that share something with it."""
        ranked = [(relevance(features, rule["conditions"], rule["statement"]), rule)
                  for rule in rules]
        ranked = [one for one in ranked if one[0] > 0]
        ranked.sort(key=lambda one: one[0], reverse=True)
        return ranked[:SHORTLIST]

    @staticmethod
    def _close_to(listed: Sequence[Tuple[float, Dict[str, Any]]], rules: List[Dict[str, Any]],
                  words: Dict[str, set]) -> List[Tuple[Dict[str, Any], str]]:
        """The lessons most alike the first few a piece of evidence was
        listed with, and not listed themselves - up to `NEIGHBOURS`, each with
        the one it is close to."""
        have = {rule["id"] for _score, rule in listed}
        found: Dict[str, Tuple[float, Dict[str, Any], str]] = {}
        for _score, near in list(listed)[:3]:
            mine = words.get(near["id"]) or rule_words(near)
            for rule in rules:
                if rule["id"] in have:
                    continue
                score = alike(mine, words.get(rule["id"]) or rule_words(rule))
                if score >= ALIKE_AT and score > found.get(rule["id"], (0.0,))[0]:
                    found[rule["id"]] = (score, rule, near["id"])
        ranked = sorted(found.values(), key=lambda one: one[0], reverse=True)[:NEIGHBOURS]
        return [(rule, near) for _score, rule, near in ranked]

    def _match(self, kind: str, rows: List[Dict[str, Any]]) -> int:
        about = KINDS.get(kind, (kind, kind))[1]
        from ...models import answers
        rules = self.rules(kind, ("candidate", "active"))
        words = {rule["id"]: rule_words(rule) for rule in rules}
        judged = 0
        for start in range(0, len(rows), 10):
            self._at(kind, "matching them to the lessons", start, len(rows))
            part = rows[start:start + 10]
            blocks, asked = {}, {}
            for row in part:
                features = _loads(row["features"], {})
                listed = self.shortlist(features, rules)
                # WEIGHED AGAINST THE LESSONS POINTING THE OTHER WAY TOO (the user, 2026-11-07),
                # in the same call: the lessons closest to those it was listed with.
                close = self._close_to(listed, rules, words)
                asked[row["id"]] = {rule["id"] for _score, rule in listed} | \
                    {rule["id"] for rule, _near in close}
                if not listed:
                    continue
                lines = [f"NEW RECORD [{row['id']}] ({row['action']}) {row['summary'][:900]}"
                         + (f"\n  the user's reason: {row['reason']}" if row["reason"] else "")]
                listed = list(listed) + [(f"close to lesson {near}", rule) for rule, near in close]
                for score, rule in listed:
                    examples = self.db.query(
                        "SELECT e.summary FROM lesson_links l JOIN lesson_evidence e ON "
                        "e.id = l.evidence_id WHERE l.rule_id = ? AND l.relation = 'supports' "
                        "ORDER BY e.at DESC LIMIT 2", (rule["id"],))
                    said = score if isinstance(score, str) else f"relevance {score}"
                    lines.append(f"  LESSON [{rule['id']}] ({said}) {rule['statement']}"
                                 f"\n    conditions: {json.dumps(rule['conditions'], ensure_ascii=False)}"
                                 + "".join(f"\n    e.g. {one['summary'][:200]}" for one in examples))
                blocks[row["id"]] = "\n".join(lines)

            def ask(ids: Sequence[str]) -> Any:
                return self._read(JUDGE_PROMPT.format(about=about, pairs="\n\n".join(
                    blocks[one] for one in ids)), language=in_english("the verdicts and why"),
                    wants=("judgements",))
            # ONLY A WHOLE ANSWER JUDGES (2026-11-07): one cut off is asked again in halves; a
            # record still not answered at its smallest is processed with no link, its failure
            # kept in its features - moved past, not asked again the same way. The model not to
            # be asked: the rest is tried with the next edit.
            unjudged: set = set()
            stop = False
            for ids, read in (answers.in_parts(list(blocks), ask) if blocks else []):
                if not read.ok and read.response is None:
                    unjudged.update(ids)
                    stop = True
                    continue
                if not read.ok:
                    log.info("the lessons failed to judge %d record(s): %s", len(ids),
                             read.why_not)
                    for row in part:
                        if row["id"] in ids:
                            self.db.execute(
                                "UPDATE lesson_evidence SET features = ? WHERE id = ?",
                                (json.dumps(dict(_loads(row["features"], {}),
                                                 match_failed=read.why_not),
                                            ensure_ascii=False), row["id"]))
                    continue
                for one in (read.data or {}).get("judgements") or []:
                    if not isinstance(one, dict):
                        continue
                    record, rule_id = str(one.get("record") or ""), str(one.get("lesson") or "")
                    relation = str(one.get("relation") or "")
                    if record not in ids or rule_id not in asked.get(record, set()) or \
                            relation not in ("supports", "contradicts"):
                        continue
                    self.db.execute(
                        "INSERT OR REPLACE INTO lesson_links (evidence_id, rule_id, relation, "
                        "why, at) VALUES (?, ?, ?, ?, ?)",
                        (record, rule_id, relation, str(one.get("why") or "")[:400], _iso()))
                    judged += 1
            for row in part:
                if row["id"] in unjudged:
                    continue
                self.db.execute("UPDATE lesson_evidence SET processed_at = ? WHERE id = ?",
                                (_iso(), row["id"]))
            if stop:
                return judged              # the rest is tried with the next edit
        return judged

    # 3. INDUCE
    def _induce(self, kind: str) -> int:
        about = KINDS.get(kind, (kind, kind))[1]
        pooled = [dict(row) for row in self.db.query(
            "SELECT * FROM lesson_evidence e WHERE kind = ? AND processed_at IS NOT NULL "
            "AND action != 'outcome' AND NOT EXISTS (SELECT 1 FROM lesson_links l WHERE "
            "l.evidence_id = e.id) ORDER BY at", (kind,))]
        for row in pooled:
            row["features"] = _loads(row["features"], {})
        made = 0
        used: set = set()
        for row in pooled:
            if row["id"] in used:
                continue
            as_rule = {"source": row["features"].get("source"),
                       "event_type": row["features"].get("event_type"),
                       "person": row["features"].get("person"),
                       "action": row["features"].get("action"),
                       "keywords": row["features"].get("keywords")}
            group = [one for one in pooled if one["id"] not in used and (
                one is row or relevance(one["features"], as_rule) >= GROUP_SCORE
                or (row.get("category") and one.get("category") == row["category"]))]
            if len(group) < GROUP_AT:
                continue
            records = "\n".join(f"[{one['id']}] ({one['action']}) {one['summary'][:600]}"
                                + (f" - the user's reason: {one['reason']}" if one["reason"]
                                   else "") for one in group[:12])
            said = self._ask(INDUCE_PROMPT.format(about=about, records=records),
                             language=in_english("the statement")
                             + in_english("its conditions and keywords"))
            if said is None:
                return made
            for one in group:
                used.add(one["id"])
            rule = said.get("rule") if isinstance(said.get("rule"), dict) else None
            if not rule or not str(rule.get("statement") or "").strip():
                continue
            explains = {str(one) for one in said.get("explains") or []} & \
                {one["id"] for one in group}
            if not explains:
                continue
            rule_id = self._new_rule(kind, rule, "formed from what the user did")
            for evidence in explains:
                self.db.execute("INSERT OR REPLACE INTO lesson_links (evidence_id, rule_id, "
                                "relation, why, at) VALUES (?, ?, 'supports', ?, ?)",
                                (evidence, rule_id, "one of the cases it was formed from",
                                 _iso()))
            made += 1
        return made

    def _new_rule(self, kind: str, rule: Dict[str, Any], why: str,
                  state: str = "candidate") -> str:
        from ...common.events.models import new_id
        ident = new_id("les")
        conditions = rule.get("conditions") if isinstance(rule.get("conditions"), dict) else {}
        at = _iso()
        self.db.execute(
            "INSERT INTO lesson_rules (id, kind, statement, conditions, state, pinned, support, "
            "contradict, confidence, created_at, updated_at, history) VALUES (?, ?, ?, ?, ?, 0, "
            "0, 0, 0.5, ?, ?, ?)",
            (ident, kind, " ".join(str(rule["statement"]).split())[:600],
             json.dumps(conditions, ensure_ascii=False), state, at, at,
             json.dumps([{"at": at, "what": "made", "why": why}], ensure_ascii=False)))
        return ident

    # 4. SCORE
    def score(self, kind: str) -> None:
        now = _now()
        for rule in self.rules(kind, ("candidate", "active", "retired")):
            support = against = 0.0
            for row in self.db.query(
                    "SELECT l.relation, e.weight, e.at FROM lesson_links l JOIN lesson_evidence e "
                    "ON e.id = l.evidence_id WHERE l.rule_id = ?", (rule["id"],)):
                when = _when(row["at"]) or now
                age = max(0.0, (now - when).total_seconds() / 86400)
                weight = float(row["weight"] or 0) * 0.5 ** (age / HALF_LIFE_DAYS)
                if row["relation"] == "supports":
                    support += weight
                else:
                    against += weight
            confidence = (support + 1) / (support + against + 2)
            self.db.execute("UPDATE lesson_rules SET support = ?, contradict = ?, confidence = ? "
                            "WHERE id = ?", (round(support, 4), round(against, 4),
                                             round(confidence, 4), rule["id"]))

    # 5. DECIDE
    def _decide(self, kind: str) -> Dict[str, int]:
        done = {"promoted": 0, "revised": 0, "merged": 0, "retired": 0}
        done.update(self._candidates(kind))
        for rule in self.rules(kind, ("active",)):
            if not self._the_users(rule) and rule["confidence"] < REVISE_BELOW:
                if self._revise(kind, rule):
                    done["revised"] += 1
        done["merged"] = self._merge(kind)
        stale = _iso(_now() - timedelta(days=STALE_DAYS))
        for rule in self.rules(kind, ("active",)):
            if self._the_users(rule) or rule["support"] >= 1:
                continue
            last = self.db.query_one(
                "SELECT MAX(e.at) AS at FROM lesson_links l JOIN lesson_evidence e ON "
                "e.id = l.evidence_id WHERE l.rule_id = ? AND l.relation = 'supports'",
                (rule["id"],))
            if not last or not last.get("at") or last["at"] < stale:
                self._change(rule, {"state": "retired"},
                             f"no support for {STALE_DAYS} days")
                done["retired"] += 1
        return done

    def _candidates(self, kind: str) -> Dict[str, int]:
        """The candidates decided: promoted on enough support - or RETIRED
        WHEN CONTRADICTED MORE THAN SUPPORTED (the user, 2026-11-07), by the
        weights as `score` counts them. One with no new evidence for long
        stays ("很久没有新证据可以留着")."""
        done = {"promoted": 0, "retired_candidates": 0}
        least = PROMOTE_BY_KIND.get(kind, PROMOTE_SUPPORT)
        sure = PROMOTE_CONFIDENCE_BY_KIND.get(kind, PROMOTE_CONFIDENCE)
        for rule in self.rules(kind, ("candidate",)):
            if rule["support"] >= least and rule["confidence"] >= sure:
                self._change(rule, {"state": "active"},
                             f"promoted: {round(rule['support'], 1)} weighted cases, "
                             f"{round(rule['confidence'] * 100)}% sure")
                done["promoted"] += 1
            elif rule["contradict"] > rule["support"] and not self._the_users(rule):
                self._change(rule, {"state": "retired"},
                             f"retired: contradicted more than supported "
                             f"({round(rule['contradict'], 2)} against, "
                             f"{round(rule['support'], 2)} for)")
                done["retired_candidates"] += 1
        return done

    def _the_users(self, rule: Dict[str, Any]) -> bool:
        """A rule the user pinned, stated in the chat or worded themselves:
        never rewritten or retired by the model (the user, 2026-11-07)."""
        if rule.get("pinned"):
            return True
        for one in rule.get("history") or []:
            why = str((one or {}).get("why") or "") if isinstance(one, dict) else ""
            if why == "stated by the user in the chat" or why.startswith("the user: edit"):
                return True
        return self.db.query_one("SELECT 1 FROM lesson_links WHERE rule_id = ? AND why = "
                                 "'the user stated it'", (rule["id"],)) is not None

    def _revise(self, kind: str, rule: Dict[str, Any]) -> bool:
        about = KINDS.get(kind, (kind, kind))[1]

        def cases(relation: str) -> str:
            rows = self.db.query(
                "SELECT e.summary, e.reason FROM lesson_links l JOIN lesson_evidence e ON "
                "e.id = l.evidence_id WHERE l.rule_id = ? AND l.relation = ? ORDER BY e.at DESC "
                "LIMIT 12", (rule["id"], relation))
            return "\n".join(f"- {one['summary'][:400]}"
                             + (f" (reason: {one['reason']})" if one["reason"] else "")
                             for one in rows) or "(none)"

        said = self._ask(REVISE_PROMPT.format(
            about=about, statement=rule["statement"],
            conditions=json.dumps(rule["conditions"], ensure_ascii=False),
            supporting=cases("supports"), contradicting=cases("contradicts")),
            language=in_english("each statement")
            + in_english("their conditions and keywords, and why"))
        if said is None:
            return False
        decision = str(said.get("decision") or "")
        why = str(said.get("why") or "")[:400]
        given = [one for one in said.get("rules") or []
                 if isinstance(one, dict) and str(one.get("statement") or "").strip()]
        evidence = [row["evidence_id"] for row in self.db.query(
            "SELECT evidence_id FROM lesson_links WHERE rule_id = ?", (rule["id"],))]
        if decision == "narrow" and given:
            self._change(rule, {"statement": " ".join(str(given[0]["statement"]).split())[:600],
                                "conditions": json.dumps(given[0].get("conditions") or {},
                                                         ensure_ascii=False)},
                         f"narrowed: {why}")
            # Read again against the narrowed lesson.
            self.db.execute("DELETE FROM lesson_links WHERE rule_id = ? AND relation = "
                            "'contradicts'", (rule["id"],))
            self._read_again(rule["id"], evidence)
            return True
        if decision == "split" and given and kind == "work":
            decision = "narrow"
        if decision == "narrow" and given and kind == "work":
            self._change(rule, {"statement": " ".join(str(given[0]["statement"]).split())[:600]},
                         f"narrowed: {why}")
            self.db.execute("DELETE FROM lesson_links WHERE rule_id = ? AND relation = "
                            "'contradicts'", (rule["id"],))
            return True
        if decision == "split" and given:
            for one in given[:3]:
                self._new_rule(kind, one, f"split from {rule['id']}: {why}", state="candidate")
            self._change(rule, {"state": "retired"}, f"split: {why}")
            self._read_again(rule["id"], evidence)
            return True
        if decision == "retire":
            self._change(rule, {"state": "retired"}, f"retired: {why}")
            return True
        return False

    def _read_again(self, rule_id: str, evidence: List[str]) -> None:
        """Evidence that rested on a rule now changed, matched again."""
        for one in evidence:
            self.db.execute("DELETE FROM lesson_links WHERE evidence_id = ? AND rule_id = ?",
                            (one, rule_id))
            self.db.execute("UPDATE lesson_evidence SET processed_at = NULL WHERE id = ? "
                            "AND action != 'outcome'", (one,))

    def _merge(self, kind: str) -> int:
        rules = self.rules(kind, ("active",))
        supports = {rule["id"]: {row["evidence_id"] for row in self.db.query(
            "SELECT evidence_id FROM lesson_links WHERE rule_id = ? AND relation = 'supports'",
            (rule["id"],))} for rule in rules}
        merged = 0
        gone: set = set()
        for i, a in enumerate(rules):
            for b in rules[i + 1:]:
                if a["id"] in gone or b["id"] in gone:
                    continue
                left, right = supports[a["id"]], supports[b["id"]]
                if not left or not right:
                    continue
                if len(left & right) / min(len(left), len(right)) < MERGE_OVERLAP:
                    continue
                if self._the_users(a) and self._the_users(b):
                    continue
                said = self._ask(MERGE_PROMPT.format(a=a["statement"], b=b["statement"]),
                                 language=in_english("the statement"))
                if not said or not said.get("same"):
                    continue
                keep, drop = self._keeper(a, b)
                self._join(kind, keep, drop, str(said.get("statement") or ""), "")
                gone.add(drop["id"])
                merged += 1
        if merged:
            self.score(kind)
        return merged

    def _keeper(self, a: Dict[str, Any], b: Dict[str, Any]
                ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Of two rules merged, the one kept: the user's own, then the one in
        force, then the better backed."""
        def rank(rule: Dict[str, Any]) -> Tuple[int, int, float]:
            return (int(self._the_users(rule)), int(rule["state"] == "active"),
                    float(rule["support"]))
        return (a, b) if rank(a) >= rank(b) else (b, a)

    def _join(self, kind: str, keep: Dict[str, Any], drop: Dict[str, Any], statement: str,
              why: str) -> None:
        """Two rules made one: the evidence and history of `drop` joined onto
        `keep` - its statement rewritten when one is given and it is not the
        user's, nor a nature of the work (kept as the nature is titled) - and
        `drop` marked merged into it."""
        for row in self.db.query("SELECT * FROM lesson_links WHERE rule_id = ?", (drop["id"],)):
            self.db.execute("INSERT OR IGNORE INTO lesson_links (evidence_id, rule_id, "
                            "relation, why, at) VALUES (?, ?, ?, ?, ?)",
                            (row["evidence_id"], keep["id"], row["relation"], row["why"],
                             row["at"]))
        statement = " ".join(str(statement or "").split())[:600]
        said = f": {why}" if why else ""
        fields: Dict[str, Any] = {}
        if statement and statement != keep["statement"] and not self._the_users(keep) \
                and kind != "work":
            fields["statement"] = statement
        if drop["state"] == "active" and keep["state"] == "candidate":
            fields["state"] = "active"      # one of them was already in force
        joined = [dict(one, joined_from=drop["id"]) for one in drop.get("history") or []
                  if isinstance(one, dict)]
        self._change(keep, fields, f"merged with {drop['id']}{said}", joined=joined)
        self._change(drop, {"state": "retired", "pinned": 0}, f"merged into {keep['id']}{said}",
                     merged_into=keep["id"])

    def _change(self, rule: Dict[str, Any], fields: Dict[str, Any], why: str,
                joined: Sequence[Dict[str, Any]] = (), merged_into: str = "") -> None:
        history = list(rule.get("history") or [])
        if joined:
            history = sorted(history + list(joined), key=lambda one: str(
                one.get("at") or "" if isinstance(one, dict) else ""))
        history.append({"at": _iso(), "what": ", ".join(sorted(fields)),
                        "before": {key: rule.get(key) for key in fields if key in rule
                                   and key != "conditions"},
                        "why": why, **({"merged_into": merged_into} if merged_into else {})})
        fields = dict(fields, updated_at=_iso(), history=json.dumps(history[-40:],
                                                                    ensure_ascii=False))
        sets = ", ".join(f"{key} = ?" for key in fields)
        self.db.execute(f"UPDATE lesson_rules SET {sets} WHERE id = ?",
                        (*fields.values(), rule["id"]))

    # 7. THE UPKEEP (module docstring; the user, 2026-11-07) ---------------------------------
    #: Config key: what the rules and the waiting evidence were at the last whole upkeep.
    UPKEPT = "lessons_upkept_{kind}"
    #: kv key prefix, then the kind: the questions the upkeep asked, each once.
    ASKED_KEY = "lessons.upkeep.asked."

    def _retakeable(self, kind: str) -> List[Dict[str, Any]]:
        """Evidence read `RETAKE_AFTER_DAYS` ago or more, linked to no rule,
        and not taken up again yet."""
        until = _iso(_now() - timedelta(days=RETAKE_AFTER_DAYS))
        rows = self.db.query(
            "SELECT id, action, summary, reason, features FROM lesson_evidence e WHERE kind = ? "
            "AND processed_at IS NOT NULL "
            "AND processed_at <= ? AND action != 'outcome' AND NOT EXISTS (SELECT 1 FROM "
            "lesson_links l WHERE l.evidence_id = e.id) ORDER BY at, rowid", (kind, until))
        return [dict(row) for row in rows if "retaken" not in _loads(row["features"], {})]

    def _upkeep_signature(self, kind: str) -> str:
        import hashlib
        rules = sorted((rule["id"], rule["statement"], rule["state"])
                       for rule in self.rules(kind, ("candidate", "active")))
        said = json.dumps([rules, len(self._retakeable(kind))], ensure_ascii=False)
        return hashlib.sha1(said.encode("utf-8")).hexdigest()

    def _upkeep_due(self, kind: str) -> bool:
        if self.config is None:
            return True
        return str(self.config.get(self.UPKEPT.format(kind=kind), "") or "") != \
            self._upkeep_signature(kind)

    def _asked(self, kind: str) -> Dict[str, Any]:
        row = self.db.query_one("SELECT value FROM kv WHERE key = ?", (self.ASKED_KEY + kind,))
        return _loads((row or {}).get("value"), {})

    def _remember(self, kind: str, asked: Dict[str, Any]) -> None:
        if len(asked) > MOST_ASKED:
            kept = sorted(asked.items(), key=lambda one: str((one[1] or {}).get("at") or ""))
            asked = dict(kept[-MOST_ASKED:])
        self._kv_set(self.ASKED_KEY + kind, json.dumps(asked, ensure_ascii=False))

    @staticmethod
    def _pair_key(step: str, a: Dict[str, Any], b: Dict[str, Any]) -> str:
        """One question: a step and two rules as they are worded - worded
        again, they are a new question."""
        import hashlib
        said = json.dumps([step] + sorted([[a["id"], a["statement"]], [b["id"], b["statement"]]]),
                          ensure_ascii=False)
        return hashlib.sha1(said.encode("utf-8")).hexdigest()[:24]

    def upkeep(self, kind: str) -> Dict[str, Any]:
        """THE UPKEEP of one kind's rules (module docstring): evidence no
        rule explains taken up again, near-duplicates merged, rules pointing
        opposite ways settled - in batches, each question once. What could
        not be answered whole is in the report's `failed`, and not asked
        again the same way."""
        with self._lock:
            self._learning = kind
            self._at(kind, "keeping the lessons in order")
            report: Dict[str, Any] = {"kind": kind, "retaken": 0, "linked": 0, "made": 0,
                                      "merged": 0, "opposed": 0, "failed": [], "more": False}
            asked = self._asked(kind)
            stopped = False
            try:
                stopped = self._retake(kind, report)
                for step in ("duplicates", "opposites"):
                    if not stopped:
                        stopped = self._pairs(kind, step, asked, report)
            finally:
                self._remember(kind, asked)
            for one in report["failed"]:
                log.info("keeping the %s lessons: %s failed: %s", kind, one.get("what"),
                         one.get("why"))
            self.score(kind)
            report.update(self._candidates(kind))
            changed = any(report.get(key) for key in ("linked", "made", "merged", "opposed",
                                                      "promoted", "retired_candidates"))
            if changed and kind == "work" and self.natures is not None:
                report["written_back"] = self.natures.write_back(self)
            if self.config is not None:
                # Not asked, or more left: due again with the next round.
                self.config.set(self.UPKEPT.format(kind=kind),
                                "" if stopped or report["more"] else self._upkeep_signature(kind),
                                internal=True)
            report["stopped"] = stopped
            self._finished(kind)
            return report

    def _shown(self, rule: Dict[str, Any], users: bool) -> str:
        """A rule as the upkeep shows it: its id, state, how backed, whose,
        its conditions and two of its cases."""
        examples = self.db.query(
            "SELECT e.summary FROM lesson_links l JOIN lesson_evidence e ON e.id = l.evidence_id "
            "WHERE l.rule_id = ? AND l.relation = 'supports' ORDER BY e.at DESC LIMIT 2",
            (rule["id"],))
        return (f"[{rule['id']}] ({rule['state']}; {round(rule['support'], 2)} for, "
                f"{round(rule['contradict'], 2)} against{'; SET BY THE USER' if users else ''}) "
                f"{rule['statement']}"
                f"\n    conditions: {json.dumps(rule['conditions'], ensure_ascii=False)}"
                + "".join(f"\n    e.g. {one['summary'][:200]}" for one in examples))

    # RETAKE
    def _retake(self, kind: str, report: Dict[str, Any]) -> bool:
        """Evidence no rule explains, taken up again ONCE each (the user,
        2026-11-07): linked to a rule it fits, or a candidate made from it.
        True when the model could not be asked."""
        from ...models import answers
        about = KINDS.get(kind, (kind, kind))[1]
        rows = self._retakeable(kind)
        if len(rows) > MOST_RETAKE:
            report["more"] = True
            rows = rows[:MOST_RETAKE]
        if not rows:
            return False
        rules = self.rules(kind, ("candidate", "active"))
        blocks: Dict[str, str] = {}
        offered: Dict[str, set] = {}
        for row in rows:
            listed = [rule for _score, rule in self.shortlist(_loads(row["features"], {}),
                                                              rules)[:5]]
            offered[row["id"]] = {rule["id"] for rule in listed}
            blocks[row["id"]] = "\n".join(
                [f"RECORD [{row['id']}] ({row['action']}) {row['summary'][:900]}"
                 + (f"\n  the user's reason: {row['reason']}" if row["reason"] else "")]
                + [f"  LESSON [{rule['id']}] {rule['statement']}\n    conditions: "
                   f"{json.dumps(rule['conditions'], ensure_ascii=False)}" for rule in listed])
        # The work's natures come from its stages (experience/stage_natures.py): none made here.
        making = kind != "work"
        new = ('  - "new": no listed lesson fits; state the rule it suggests, as narrow as it '
               'needs - a possible\n    rule, kept forming until more cases back it.\n'
               if making else "")
        none = ("it says nothing general about how the user wants things done." if making
                else "no listed lesson fits it.")

        def ask(ids: Sequence[str]) -> Any:
            return self._read(RETAKE_PROMPT.format(
                about=about, new=new, none=none, new_word="new|" if making else "",
                records="\n\n".join(blocks[one] for one in ids)),
                language=in_english("each statement")
                + in_english("their conditions and keywords, and why"), wants=("records",))
        ids = [row["id"] for row in rows]
        features = {row["id"]: _loads(row["features"], {}) for row in rows}

        def mark(ident: str, said: str) -> None:
            self.db.execute("UPDATE lesson_evidence SET features = ? WHERE id = ?",
                            (json.dumps(dict(features[ident], retaken={"at": _iso(), "as": said}),
                                        ensure_ascii=False), ident))
        for start in range(0, len(ids), UPKEEP_BATCH):
            for part, read in answers.in_parts(ids[start:start + UPKEEP_BATCH], ask):
                if not read.ok and read.response is None:
                    return True            # not asked: the rest with the next upkeep
                if not read.ok:
                    # ONCE (the user, 2026-11-07): failed at its smallest, recorded and moved past.
                    report["failed"].append({"what": f"taking up {len(part)} record(s) again",
                                             "why": read.why_not})
                    for ident in part:
                        mark(ident, f"failed: {read.why_not}")
                    continue
                said = (read.data or {}).get("records")
                answered = {str(one.get("record") or ""): one for one in
                            (said if isinstance(said, list) else []) if isinstance(one, dict)}
                for ident in part:
                    one = answered.get(ident)
                    if one is None:
                        report["failed"].append({"what": f"taking up {ident} again",
                                                 "why": "the answer did not say"})
                        mark(ident, "failed: the answer did not say")
                        continue
                    report["retaken"] += 1
                    mark(ident, self._take_up(kind, ident, one, offered[ident], making, report))
        return False

    def _take_up(self, kind: str, ident: str, one: Dict[str, Any], offered: set, making: bool,
                 report: Dict[str, Any]) -> str:
        decision = str(one.get("decision") or "")
        why = " ".join(str(one.get("why") or "").split())[:300]
        if decision == "fits":
            rule_id, relation = str(one.get("lesson") or ""), str(one.get("relation") or "")
            if rule_id not in offered or relation not in ("supports", "contradicts"):
                return "none: no lesson listed for it named"
            self.db.execute("INSERT OR REPLACE INTO lesson_links (evidence_id, rule_id, relation, "
                            "why, at) VALUES (?, ?, ?, ?, ?)",
                            (ident, rule_id, relation, f"taken up again: {why}"[:400], _iso()))
            report["linked"] += 1
            return f"{relation} {rule_id}"
        rule = one.get("rule") if isinstance(one.get("rule"), dict) else {}
        if decision == "new" and making and str(rule.get("statement") or "").strip():
            rule_id = self._new_rule(kind, rule, f"made from a case no lesson explained, taken "
                                                 f"up again: {why}")
            self.db.execute("INSERT OR REPLACE INTO lesson_links (evidence_id, rule_id, relation, "
                            "why, at) VALUES (?, ?, 'supports', ?, ?)",
                            (ident, rule_id, "the case it was made from", _iso()))
            report["made"] += 1
            return f"made {rule_id}"
        return "none"

    # DUPLICATES, OPPOSITES
    def _pairs(self, kind: str, step: str, asked: Dict[str, Any], report: Dict[str, Any]) -> bool:
        """Rules alike enough - worded much the same (`duplicates`), or about
        the same thing (`opposites`) - shown to the model in pairs, in
        batches; what it decides done, each with why in the rule's history.
        True when the model could not be asked."""
        from ...models import answers
        about = KINDS.get(kind, (kind, kind))[1]
        rules = self.rules(kind, ("candidate", "active"))
        words = {rule["id"]: rule_words(rule) for rule in rules}
        users = {rule["id"]: self._the_users(rule) for rule in rules}
        least = DUPLICATE_AT if step == "duplicates" else ALIKE_AT
        found = []
        for i, a in enumerate(rules):
            for b in rules[i + 1:]:
                if users[a["id"]] and users[b["id"]]:
                    continue                   # the user's say, both: nothing for the model
                score = alike(words[a["id"]], words[b["id"]])
                if score < least:
                    continue
                key = self._pair_key(step, a, b)
                if key not in asked:
                    found.append((score, key, a, b))
        found.sort(key=lambda one: one[0], reverse=True)
        if len(found) > MOST_PAIRS:
            report["more"] = True
            found = found[:MOST_PAIRS]
        if not found:
            return False
        pairs = {f"p{n + 1}": (key, a, b) for n, (_score, key, a, b) in enumerate(found)}

        def ask(ids: Sequence[str]) -> Any:
            shown = "\n\n".join(
                f"PAIR [{pid}]"
                + "".join(f"\n  {side} {self._shown(rule, users[rule['id']])}"
                          for side, rule in (("A", pairs[pid][1]), ("B", pairs[pid][2])))
                for pid in ids)
            prompt = DUPLICATES_PROMPT if step == "duplicates" else OPPOSITES_PROMPT
            return self._read(prompt.format(about=about, pairs=shown),
                              language=in_english("each statement")
                              + in_english("their conditions and keywords, and why"),
                              wants=("pairs",))
        touched: set = set()
        ids = list(pairs)
        for start in range(0, len(ids), UPKEEP_BATCH):
            for part, read in answers.in_parts(ids[start:start + UPKEEP_BATCH], ask):
                if not read.ok and read.response is None:
                    return True            # not asked: the rest with the next upkeep
                if not read.ok:
                    report["failed"].append({"what": f"{step}: {len(part)} pair(s)",
                                             "why": read.why_not})
                    for pid in part:
                        asked[pairs[pid][0]] = {"at": _iso(), "step": step,
                                                "as": f"failed: {read.why_not}"}
                    continue
                said = (read.data or {}).get("pairs")
                answered = {str(one.get("pair") or ""): one for one in
                            (said if isinstance(said, list) else []) if isinstance(one, dict)}
                for pid in part:
                    key, a, b = pairs[pid]
                    if a["id"] in touched or b["id"] in touched:
                        report["more"] = True  # changed by another pair: asked as it is now
                        continue
                    one = answered.get(pid)
                    if one is None:
                        result = "failed: the answer did not say"
                    elif step == "duplicates":
                        result = self._settle_duplicate(kind, a, b, one, users, report)
                    else:
                        result = self._settle_opposites(kind, a, b, one, users, report)
                    if result.startswith("failed"):
                        report["failed"].append({"what": f"{step}: {a['id']} and {b['id']}",
                                                 "why": result[len("failed: "):]})
                    elif not result.startswith(("none", "left", "not")):
                        touched.update((a["id"], b["id"]))
                    asked[key] = {"at": _iso(), "step": step, "as": result[:200]}
        return False

    def _settle_duplicate(self, kind: str, a: Dict[str, Any], b: Dict[str, Any],
                          one: Dict[str, Any], users: Dict[str, bool],
                          report: Dict[str, Any]) -> str:
        if one.get("same") is not True:
            return "not the same"
        why = " ".join(str(one.get("why") or "").split())[:300]
        keep, drop = self._keeper(a, b)
        self._join(kind, keep, drop, str(one.get("statement") or ""),
                   f"the same lesson{': ' + why if why else ''}")
        report["merged"] += 1
        return f"merged {drop['id']} into {keep['id']}"

    def _settle_opposites(self, kind: str, a: Dict[str, Any], b: Dict[str, Any],
                          one: Dict[str, Any], users: Dict[str, bool],
                          report: Dict[str, Any]) -> str:
        decision = str(one.get("decision") or "")
        why = " ".join(str(one.get("why") or "").split())[:300]
        if decision == "none":
            return "none"
        if decision == "merge":
            keep, drop = self._keeper(a, b)
            self._join(kind, keep, drop, str(one.get("statement") or ""),
                       f"pointing opposite ways, one lesson after all{': ' + why if why else ''}")
            report["opposed"] += 1
            return f"merged {drop['id']} into {keep['id']}"
        if decision not in ("narrow", "retire"):
            return "failed: no decision"
        named = str(one.get("lesson") or "")
        target = a if named == a["id"] else b if named == b["id"] else None
        if target is None:
            return "failed: named neither lesson"
        if users[target["id"]]:
            return "left: the user's own"     # the user's say wins (2026-11-07)
        other = b if target is a else a
        if decision == "retire":
            self._change(target, {"state": "retired"},
                         f"retired: it points opposite to {other['id']}: {why}")
            report["opposed"] += 1
            return f"retired {target['id']}"
        statement = " ".join(str(one.get("statement") or "").split())[:600]
        if not statement:
            return "failed: no narrowed statement"
        fields: Dict[str, Any] = {"statement": statement}
        if kind != "work" and isinstance(one.get("conditions"), dict):
            fields["conditions"] = json.dumps(one["conditions"], ensure_ascii=False)
        # "narrowed" first: a nature of the work is written back with its new words (natures.py).
        self._change(target, fields, f"narrowed: against {other['id']}, pointing the other way: "
                                     f"{why}")
        report["opposed"] += 1
        return f"narrowed {target['id']}"

    # -- the model -------------------------------------------------------------------------
    def language(self) -> str:
        if self._language is not None:
            try:
                return self._language()
            except Exception:                  # pragma: no cover
                pass
        return "English"

    def _ask(self, prompt: str, language: Any = None) -> Optional[Dict[str, Any]]:
        """The JSON of a whole answer, or None: cut off at the limit, unreadable or not asked
        (2026-11-07: an answer read as {} was taken as "nothing in it")."""
        read = self._read(prompt, language=language)
        return read.data if read.ok else None

    def _read(self, prompt: str, language: Any = None, wants: Sequence[str] = ()) -> Any:
        """The model's answer, read (models/answers.py): `.ok` only when whole, readable and
        saying every key in `wants`."""
        from ...models import answers
        if self.models is None:
            return answers.Answer(data=None, cut=False, text="")
        from ...models.base import ModelRequest
        try:
            response = self.models.invoke(ModelRequest(
                messages=[{"role": "user", "content": prompt}],
                system="You keep the lessons of a personal agent, faithfully and briefly. "
                       "Return only the JSON object asked for.",
                # Counted under the module of the kind being learned.
                max_tokens=4000, task=f"lessons_{getattr(self, '_learning', '') or 'other'}",
                language=language),
                role="heartbeat")
        except Exception as exc:
            log.info("the lessons could not be asked: %s", exc)
            return answers.Answer(data=None, cut=False, text="")
        return answers.read(response, parse=answers.json_of, wants=wants)


__all__ = ["LessonEngine", "relevance", "SHORTLIST", "KINDS"]
