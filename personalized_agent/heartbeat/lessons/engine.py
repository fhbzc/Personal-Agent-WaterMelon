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
             disabled - with their reason when they gave one; and among it,
             the user undoing what the agent did on a lesson (`used`,
             `outcome`). What the agents do - a decision left standing, the
             reviewer opening work again - is never evidence.
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
               why. Evidence nothing explains goes to the pool.
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
               support for `STALE_DAYS` retires. The user's pin, rejection
               and edits win.
  6. PUBLISH   `text`: only the lessons in force, each with its id, its
               confidence and how many cases back it - what the agent is
               given, never the raw actions.
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
#: Pooled evidence sharing features, this many or more, is induced from.
GROUP_AT = 3
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

#: The most pieces of evidence one call is given.
BATCH = 30

#: What each kind of lesson is about - said to the model.
KINDS = {
    "notifications": ("Notification lessons",
                      "what the user did to the notifications and calendar events the "
                      "agent made or they made themselves"),
    "connections": ("Connection lessons",
                    "what the user did to their contacts - added, edited, deleted, merged"),
    "work": ("How you work",
             "the stages of the user's own work - what they decided, required, "
             "accepted and rejected"),
}

#: The kinds learned with the state heartbeat's round.
LEARNED_KINDS = ("notifications", "connections", "work")
#: Each kind's module (common/modules.py): one turned off is not learned.
KIND_MODULE = {"notifications": "calendar", "connections": "connections",
               "work": "experience"}

FEATURES_PROMPT = """You read records of {about}, and give each one the features it is
matched by. Return ONLY this JSON object:
{{"features": {{"<record id>": {{"source": "where it came from - an app or channel, e.g. wechat, email, screen, calendar, manual",
                                "event_type": "what kind of thing it is about, in two or three words, e.g. meeting, deadline, payment, person",
                                "person": "the person it is about or with, if any, as named",
                                "keywords": ["3 to 8 words that say what it is about"]}}}}}}

Lower case.

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
    def record(self, kind: str, source_id: str, summary: str, action: str = "",
               reason: str = "", at: str = "", weight: Optional[float] = None,
               outcome_of: str = "", learn: bool = False) -> str:
        """One piece of evidence, kept - learned from with the next round
        (or at once, with `learn`)."""
        from ...common.events.models import new_id
        reason = " ".join(str(reason or "").split())[:1000]
        if weight is None:
            weight = WEIGHT_REASON if reason else WEIGHT_ACTION
        ident = new_id("evi")
        self.db.execute(
            "INSERT INTO lesson_evidence (id, kind, source_id, at, action, summary, reason, "
            "weight, features, processed_at, outcome_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
            "NULL, NULL, ?)",
            (ident, kind, str(source_id or ""), at or _iso(), str(action or "")[:40],
             str(summary or "")[:4000], reason, float(weight), str(outcome_of or "")))
        if learn:
            self.learn_in_background(kind)
        return ident

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

    def outcome(self, kind: str, subject_id: str, summary: str) -> int:
        """The user undid what the agent did on a lesson - deleted, disabled
        or changed it: a correction, against each lesson it followed."""
        rows = self.db.query("SELECT * FROM lesson_uses WHERE kind = ? AND subject_id = ? "
                             "AND resolved_at IS NULL", (kind, str(subject_id)))
        made = 0
        for use in rows:
            for rule_id in _loads(use["rule_ids"], []):
                self._linked_evidence(kind, rule_id, "contradicts", WEIGHT_CORRECTION,
                                      f"The user corrected what the agent did on this "
                                      f"lesson: {summary}", outcome_of=use["id"])
                made += 1
            self.db.execute("UPDATE lesson_uses SET resolved_at = ? WHERE id = ?",
                            (_iso(), use["id"]))
        if made:
            self.score(kind)
        return made

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
                         summary: str, outcome_of: str) -> None:
        ident = self.record(kind, outcome_of, summary, action="outcome", weight=weight,
                            outcome_of=outcome_of, learn=False)
        self.db.execute("UPDATE lesson_evidence SET processed_at = ?, features = '{}' "
                        "WHERE id = ?", (_iso(), ident))
        self.db.execute("INSERT OR REPLACE INTO lesson_links (evidence_id, rule_id, relation, "
                        "why, at) VALUES (?, ?, ?, ?, ?)",
                        (ident, rule_id, relation, "the outcome of the agent acting on it",
                         _iso()))

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
                "e.action FROM lesson_links l JOIN lesson_evidence e ON e.id = l.evidence_id "
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
            if (waiting or {}).get("n") or fresh:
                out.append(kind)
        return out

    #: A cycle's steps, in order - its bar moves through them.
    STEPS = ("bringing in the natures", "reading the new edits", "matching them to the lessons",
             "finding new lessons", "deciding", "writing back onto the natures", "grouping")

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
        wanting = [row for row in rows if row.get("features") is None]
        for start in range(0, len(wanting), BATCH):
            part = wanting[start:start + BATCH]
            records = "\n".join(f"[{row['id']}] ({row['action']}) {row['summary'][:900]}"
                                + (f" - the user's reason: {row['reason']}" if row["reason"]
                                   else "") for row in part)
            self._at(kind, "reading the new edits", start, len(wanting))
            said = self._ask(FEATURES_PROMPT.format(about=about, records=records),
                             language=in_english("the features and keywords"))
            if said is None:
                return                      # tried again with the next edit
            got = said.get("features") if isinstance(said.get("features"), dict) else {}
            for row in part:
                one = got.get(row["id"]) if isinstance(got.get(row["id"]), dict) else {}
                features = {"source": str(one.get("source") or "")[:60].lower(),
                            "event_type": str(one.get("event_type") or "")[:60].lower(),
                            "person": str(one.get("person") or "")[:80],
                            "keywords": [str(k).lower()[:40] for k in one.get("keywords") or []][:10],
                            "action": row["action"]}
                self.db.execute("UPDATE lesson_evidence SET features = ? WHERE id = ?",
                                (json.dumps(features, ensure_ascii=False), row["id"]))

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

    def _match(self, kind: str, rows: List[Dict[str, Any]]) -> int:
        about = KINDS.get(kind, (kind, kind))[1]
        rules = self.rules(kind, ("candidate", "active"))
        judged = 0
        for start in range(0, len(rows), 10):
            self._at(kind, "matching them to the lessons", start, len(rows))
            part = rows[start:start + 10]
            blocks, asked = [], {}
            for row in part:
                features = _loads(row["features"], {})
                listed = self.shortlist(features, rules)
                asked[row["id"]] = {rule["id"] for _score, rule in listed}
                if not listed:
                    continue
                lines = [f"NEW RECORD [{row['id']}] ({row['action']}) {row['summary'][:900]}"
                         + (f"\n  the user's reason: {row['reason']}" if row["reason"] else "")]
                for score, rule in listed:
                    examples = self.db.query(
                        "SELECT e.summary FROM lesson_links l JOIN lesson_evidence e ON "
                        "e.id = l.evidence_id WHERE l.rule_id = ? AND l.relation = 'supports' "
                        "ORDER BY e.at DESC LIMIT 2", (rule["id"],))
                    lines.append(f"  LESSON [{rule['id']}] (relevance {score}) {rule['statement']}"
                                 f"\n    conditions: {json.dumps(rule['conditions'], ensure_ascii=False)}"
                                 + "".join(f"\n    e.g. {one['summary'][:200]}" for one in examples))
                blocks.append("\n".join(lines))
            if blocks:
                said = self._ask(JUDGE_PROMPT.format(about=about, pairs="\n\n".join(blocks)),
                                 language=in_english("the verdicts and why"))
                if said is None:
                    return judged          # the rest is tried with the next edit
                for one in said.get("judgements") or []:
                    if not isinstance(one, dict):
                        continue
                    record, rule_id = str(one.get("record") or ""), str(one.get("lesson") or "")
                    relation = str(one.get("relation") or "")
                    if rule_id not in asked.get(record, set()) or \
                            relation not in ("supports", "contradicts"):
                        continue
                    self.db.execute(
                        "INSERT OR REPLACE INTO lesson_links (evidence_id, rule_id, relation, "
                        "why, at) VALUES (?, ?, ?, ?, ?)",
                        (record, rule_id, relation, str(one.get("why") or "")[:400], _iso()))
                    judged += 1
            for row in part:
                self.db.execute("UPDATE lesson_evidence SET processed_at = ? WHERE id = ?",
                                (_iso(), row["id"]))
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
                one is row or relevance(one["features"], as_rule) >= GROUP_SCORE)]
            if len(group) < GROUP_AT:
                continue
            records = "\n".join(f"[{one['id']}] ({one['action']}) {one['summary'][:600]}"
                                + (f" - the user's reason: {one['reason']}" if one["reason"]
                                   else "") for one in group[:12])
            said = self._ask(INDUCE_PROMPT.format(about=about, records=records),
                             language=in_display("the statement")
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
        least = PROMOTE_BY_KIND.get(kind, PROMOTE_SUPPORT)
        sure = PROMOTE_CONFIDENCE_BY_KIND.get(kind, PROMOTE_CONFIDENCE)
        for rule in self.rules(kind, ("candidate",)):
            if rule["support"] >= least and rule["confidence"] >= sure:
                self._change(rule, {"state": "active"},
                             f"promoted: {round(rule['support'], 1)} weighted cases, "
                             f"{round(rule['confidence'] * 100)}% sure")
                done["promoted"] += 1
        for rule in self.rules(kind, ("active",)):
            if not rule["pinned"] and rule["confidence"] < REVISE_BELOW:
                if self._revise(kind, rule):
                    done["revised"] += 1
        done["merged"] = self._merge(kind)
        stale = _iso(_now() - timedelta(days=STALE_DAYS))
        for rule in self.rules(kind, ("active",)):
            if rule["pinned"] or rule["support"] >= 1:
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
            language=in_display("each statement")
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
                said = self._ask(MERGE_PROMPT.format(a=a["statement"], b=b["statement"]),
                                 language=in_display("the statement"))
                if not said or not said.get("same"):
                    continue
                keep, drop = (a, b) if a["support"] >= b["support"] else (b, a)
                for row in self.db.query("SELECT * FROM lesson_links WHERE rule_id = ?",
                                         (drop["id"],)):
                    self.db.execute("INSERT OR IGNORE INTO lesson_links (evidence_id, rule_id, "
                                    "relation, why, at) VALUES (?, ?, ?, ?, ?)",
                                    (row["evidence_id"], keep["id"], row["relation"],
                                     row["why"], row["at"]))
                if str(said.get("statement") or "").strip() and not keep["pinned"]:
                    self._change(keep, {"statement": " ".join(str(said["statement"]).split())[:600]},
                                 f"merged with {drop['id']}")
                self._change(drop, {"state": "retired", "pinned": 0},
                             f"merged into {keep['id']}")
                gone.add(drop["id"])
                merged += 1
        if merged:
            self.score(kind)
        return merged

    def _change(self, rule: Dict[str, Any], fields: Dict[str, Any], why: str) -> None:
        history = list(rule.get("history") or [])
        history.append({"at": _iso(), "what": ", ".join(sorted(fields)),
                        "before": {key: rule.get(key) for key in fields if key in rule
                                   and key != "conditions"},
                        "why": why})
        fields = dict(fields, updated_at=_iso(), history=json.dumps(history[-40:],
                                                                    ensure_ascii=False))
        sets = ", ".join(f"{key} = ?" for key in fields)
        self.db.execute(f"UPDATE lesson_rules SET {sets} WHERE id = ?",
                        (*fields.values(), rule["id"]))

    # -- the model -------------------------------------------------------------------------
    def language(self) -> str:
        if self._language is not None:
            try:
                return self._language()
            except Exception:                  # pragma: no cover
                pass
        return "English"

    def _ask(self, prompt: str, language: Any = None) -> Optional[Dict[str, Any]]:
        if self.models is None:
            return None
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
            return None
        return _json_of(getattr(response, "text", "") or "")


__all__ = ["LessonEngine", "relevance", "SHORTLIST", "KINDS"]
