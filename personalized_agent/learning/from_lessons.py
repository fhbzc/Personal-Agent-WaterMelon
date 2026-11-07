"""EVERY LESSON LEARNED FROM, NOT ONLY THE WORK (the user, 2026-11-06: "learning应该对所有lessons都学，
并且可以跨lesson的分析"). What the user answered and did - to notifications, recommendations, their
contacts and their lists - comes into the learning as judgments like those read from their work, so
that the reasons are looked for across all of it.

  - THE UNIT `lessons`, turned off or deleted as any other, and then nothing of it is taken or given.
  - A CASE for each kind of thing told (a notification of one class), a JUDGMENT for each answer, with
    the moment it came at (heartbeat/lessons/moment.py). Only what the user did: what the agents did
    is not a lesson (heartbeat/lessons/engine.py).
  - THE WORK'S OWN LESSONS ARE NOT TAKEN ONE BY ONE: they are the stages of work the learning reads
    already; it comes in through its reasons and principles (`Reasons.across`).
  - BEFORE ANSWERS WERE KEPT (2026-11-04) only deleting and calling something bad left a mark, and
    that is said with each, or a reason would learn that nearly everything was unwelcome.

A REASON SAYS WHERE IT CAME FROM (the user, 2026-11-06: not a filter): whoever is given one - a work
run too - is told which answers it was learned from (`learned_from`), and judges for itself whether
it bears on what it is doing. "Not in the evening" means something else once it says it came of
answering reminders.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .store import LearningStore, now
from .vocab import Vocabulary, term_id

log = logging.getLogger(__name__)

UNIT = "lessons"
KINDS = ("notifications", "recommendations", "connections", "general")
#: Until this day an answer that was not a deletion or a Bad left no mark.
ANSWERS_KEPT_FROM = "2026-11-04"
#: What each kind is, in the words of the list; what it is about.
DELIVERABLE = {"notifications": "notification", "recommendations": "recommendation",
               "connections": "other deliverable", "general": "other deliverable"}
OBJECT = {"notifications": "interruptions", "recommendations": "what is recommended",
          "connections": "people", "general": "how work is listed"}
#: An answer: (aspect, what it says, how strongly) - and how it is scored as a prediction.
ANSWERS: Dict[str, Tuple[str, str, str, str]] = {
    "bad_notification": ("attention", "not worth telling them", "changed", "negative"),
    "delete": ("attention", "not wanted", "changed", "negative"),
    "removed_todo": ("attention", "no longer wanted on the list", "changed", "negative"),
    "remind_later": ("timing", "wanted later: the moment was wrong", "changed", "later"),
    "got_it": ("attention", "seen, no complaint", "accepted", "neutral"),
    "accept": ("attention", "taken up", "approved", "positive"),
    "good_to_know": ("attention", "glad to be told", "approved", "positive"),
    "read_detail": ("attention", "wanted more of it", "approved", "positive"),
    "add_to_collection": ("attention", "worth keeping", "approved", "positive"),
    "to_todo": ("attention", "worth doing", "approved", "positive"),
    "added_todo": ("attention", "worth doing, of their own accord", "approved", ""),
    "add": ("attention", "worth being reminded of, of their own accord", "approved", ""),
    "chat_reply": ("attention", "answered in their own words", "changed", ""),
    "ticked_step": ("attention", "marked this step done", "accepted", ""),
    "merged_projects": ("attention", "these are one piece of work", "changed", ""),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS learn_from_lessons (
    evidence_id TEXT PRIMARY KEY,
    judgment_id TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learn_predictions (
    evidence_id TEXT PRIMARY KEY,
    shown       TEXT NOT NULL DEFAULT '',
    with_reasons TEXT NOT NULL DEFAULT '',
    without     TEXT NOT NULL DEFAULT '',
    actual      TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL
);
"""


def ready(db: Any) -> None:
    with db.write() as conn:
        conn.executescript(SCHEMA)


def before_the_answer(evidence: Dict[str, Any]) -> str:
    """What was told, and the moment, without what they did: to be predicted."""
    text = " ".join(str(evidence.get("summary") or "").split())
    text = re.sub(r" - the user (answered|called it).*?(?=(At the time:|$))", " ", text)
    text = re.sub(r"^The user marked this as a bad notification\.\s*", "", text)
    return text[:700]


def _case_of(store: LearningStore, kind: str, category: str, at: str) -> str:
    title = f"{kind}: {category or 'other'}"
    for one in store.cases(UNIT):
        if one["title"] == title:
            return one["id"]
    return store.save_case({
        "unit_id": UNIT, "title": title, "began": at,
        "facets": {"deliverable": [term_id("deliverable", DELIVERABLE[kind])],
                   "audience": [term_id("audience", "self")],
                   "presentation": [term_id("presentation", "read alone")]},
        "looks_like": f"what the agent told or kept for the user: {category or kind}"})


def take(db: Any) -> List[Dict[str, Any]]:
    """The lessons not yet taken, as judgments: the evidence of each one taken. Nothing when the
    user turned this off."""
    ready(db)
    store = LearningStore(db)
    unit = store.unit(UNIT)
    if unit is None:
        store.save_unit({"id": UNIT, "kind": "lessons", "key": UNIT, "state": "read",
                         "title": "Your answers to the agent",
                         "summary": "What you answered and did with what it told you."})
    elif unit.get("state") in ("inactive", "gone"):
        return []
    Vocabulary(db).ensure_fixed()
    marks = ",".join("?" for _ in KINDS)
    rows = [dict(one) for one in db.query(
        f"SELECT * FROM lesson_evidence WHERE kind IN ({marks}) AND action != 'outcome' AND id NOT "
        "IN (SELECT evidence_id FROM learn_from_lessons) ORDER BY at", KINDS)]
    taken = []
    for one in rows:
        aspect, says, strength, _group = ANSWERS.get(
            one["action"], ("attention", f"did this: {one['action']}", "accepted", ""))
        why = before_the_answer(one)
        if one["at"] < ANSWERS_KEPT_FROM:
            why += " (From before answers were kept: only deleting or calling it bad left a mark.)"
        judgment = store.save_judgment({
            "case_id": _case_of(store, one["kind"], str(one.get("category") or ""), one["at"]),
            "item": str(one.get("category") or "")[:120], "aspect": aspect,
            "object": term_id("object", OBJECT[one["kind"]]), "direction": says,
            "said": str(one.get("reason") or "")[:500], "why": why, "strength": strength,
            "at": one["at"], "refs": [one["id"]], "state": "new"})
        db.execute("INSERT OR IGNORE INTO learn_from_lessons (evidence_id, judgment_id, at) VALUES "
                   "(?, ?, ?)", (one["id"], judgment, now()))
        taken.append(one)
    return taken


def learned_from(store: LearningStore, reason_id: str, most: int = 3) -> str:
    """Which of the user's answers a reason rests on, in words, and how much of it they are; "" for
    a reason of their work alone."""
    supports = [one for one in store.evidence(reason_id) if one["role"] == "supports"]
    answers = []
    for one in supports:
        case = store.case(one["case_id"]) or {}
        if case.get("unit_id") == UNIT:
            answers.append((one, case))
    if not answers:
        return ""
    shown = []
    for one, case in answers[-most:]:
        told = " ".join(str(one.get("why") or "").split())[:140]
        said = f' saying "{str(one["said"])[:120]}"' if one.get("said") else ""
        shown.append(f'{str(one.get("at") or "")[:10]} {case["title"]}: {told} -> {one["direction"]}{said}')
    return (f"Learned from how they answered their agent ({len(answers)} of {len(supports)} of what it "
            "rests on), not from a piece of work: " + "; ".join(shown))


def how_it_went(db: Any, recent: int = 30) -> Dict[str, Any]:
    """Whether the reasons tell what the user will answer: guessed before it was learned from,
    with them and without."""
    ready(db)
    rows = [dict(one) for one in db.query("SELECT * FROM learn_predictions ORDER BY at DESC")]
    rated = [one for one in rows if one["actual"]]
    return {"count": len(rated),
            "with_reasons": sum(one["with_reasons"] == one["actual"] for one in rated),
            "without": sum(one["without"] == one["actual"] for one in rated),
            "recent": rated[:recent]}


__all__ = ["UNIT", "KINDS", "ANSWERS", "take", "ready", "before_the_answer", "learned_from",
           "how_it_went"]
