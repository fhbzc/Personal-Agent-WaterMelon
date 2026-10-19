"""LESSON CARDS: what was asked while the user's work was done, and answered.

A Work run's reviewer meets questions the user's past work does not answer
in so many words - which charting library they use, how they name a data
file, whether a page of theirs signs people in. Each question it answers is
a CARD: the question (in English), the context it came up in, the specific
answer, and the EVIDENCE it rests on, each piece typed:

* ``user_said``     - the user's own words (a chat, a request, a comment);
* ``user_code``     - code or a document the user wrote;
* ``user_decision`` - a decision the user made in a session;
* ``model``         - the model's own knowledge, nothing of the user's;
* ``web``           - a page on the web.

WEB CONTENT IS NEVER KEPT. A card that rests on a web page, even partly, is
used in the run that found it and not written here: what a page says may be
written to steer whoever reads it (prompt injection), and a lesson is read
by every later run. So the tiers a kept card can have are two: ``user`` -
something of the user's says so - and ``assumption`` - the model's own.

A CARD IS NEVER EDITED. A change is a new version that SUPERSEDES the one
before it, with the same lineage; the old one stays, marked so.

NO FIXED SET OF TAGS. The FACETS are computed here from the evidence - the
kinds of file it points to, the stack its dependency files name, the
project it came from; the TOPICS are free words the model proposes, in
English, kept as given (lowercase, without repeats). Neither is a filter:
a card is found by its QUESTION, against the question asked (words, each
worth what it is rare among the questions kept), and a card from the same
project counts for more.

Found here, by code alone; written by the reviewer that answered it. What
is kept and how it is used is recorded (`used`) - which run, on which card
of its work, to what decision.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ...index.tokens import words as _words

LIVE = "live"

#: The kinds of evidence a card may rest on.
EVIDENCE_TYPES = ("user_said", "user_code", "user_decision", "model", "web")
#: Of the user's own.
USERS = frozenset({"user_said", "user_code", "user_decision"})
#: The states a card is in: kept and found, or not found any more.
FOUND = ("candidate", "kept")
STATUSES = (*FOUND, "superseded", "retired")

#: Words a question says that tell no question apart.
COMMON = frozenset({"the", "a", "an", "of", "to", "in", "on", "for", "and", "or",
                    "is", "are", "be", "it", "this", "that", "what", "which", "how",
                    "does", "do", "should", "with", "by", "as", "at", "from", "user",
                    "users", "their", "they", "we", "i", "you", "when", "where", "why"})
#: A card from the same project counts this much more.
SAME_PROJECT = 0.3
#: A card is a match only when it shares this share of the question's worth.
LEAST_SHARE = 0.2

#: The stack a dependency file names.
STACKS = {"package.json": "node", "requirements.txt": "python", "pyproject.toml": "python",
          "setup.py": "python", "pipfile": "python", "cargo.toml": "rust", "go.mod": "go",
          "gemfile": "ruby", "composer.json": "php", "pom.xml": "java",
          "build.gradle": "java", "description": "r", "renv.lock": "r"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(text: Any, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def tier_of(evidence: Sequence[Dict[str, Any]]) -> str:
    """``user`` when something of the user's says so; ``web`` when a page
    does (never kept); else ``assumption``."""
    kinds = {str(one.get("type") or "") for one in evidence}
    if kinds & USERS:
        return "user"
    if "web" in kinds:
        return "web"
    return "assumption"


def facets_of(evidence: Sequence[Dict[str, Any]], project: str = "") -> Dict[str, Any]:
    """What the evidence points to, by code: the kinds of file, the stack its
    dependency files name, the project it came from."""
    kinds, stack = set(), set()
    for one in evidence:
        ref = str(one.get("ref") or "").strip()
        if not ref or "://" in ref:
            continue
        name = PurePath(ref.replace("\\", "/")).name.lower()
        if name in STACKS:
            stack.add(STACKS[name])
        suffix = PurePath(name).suffix.lstrip(".")
        if suffix:
            kinds.add(suffix)
    out: Dict[str, Any] = {"files": sorted(kinds), "stack": sorted(stack)}
    if project:
        out["project"] = project
    return out


def topics_of(proposed: Iterable[Any]) -> List[str]:
    """The model's topic words as kept: lowercase, trimmed, without repeats."""
    out: List[str] = []
    for one in proposed or []:
        word = " ".join(str(one or "").lower().split())[:40]
        if word and word not in out:
            out.append(word)
    return out[:8]


def _terms(text: str) -> List[str]:
    return [w for w in dict.fromkeys(_words(text)) if w not in COMMON]


class LessonCards:
    """The lesson cards of one scope: written, superseded, found, used."""

    def __init__(self, db: Any, scope: str = LIVE) -> None:
        self.db = db
        self.scope = scope

    # -- reading ---------------------------------------------------------------------
    @staticmethod
    def _card(row: Any) -> Dict[str, Any]:
        card = dict(row)
        for key, default in (("context", {}), ("evidence", []), ("principles", []),
                             ("facets", {}), ("topics", []), ("written_by", {})):
            card[key] = _loads(card.get(key), default)
        return card

    def get(self, card_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM lesson_cards WHERE id = ?", (card_id,))
        return self._card(rows[0]) if rows else None

    def cards(self, statuses: Sequence[str] = FOUND) -> List[Dict[str, Any]]:
        """The cards of this scope in these states, newest first."""
        marks = ",".join("?" * len(statuses))
        rows = self.db.query(
            f"SELECT * FROM lesson_cards WHERE scope = ? AND status IN ({marks}) "
            "ORDER BY created_at DESC, id", (self.scope, *statuses))
        return [self._card(row) for row in rows]

    def versions(self, card_id: str) -> List[Dict[str, Any]]:
        """Every version of a card's lineage, the first first."""
        card = self.get(card_id)
        if card is None:
            return []
        rows = self.db.query("SELECT * FROM lesson_cards WHERE lineage = ? ORDER BY version",
                             (card["lineage"],))
        return [self._card(row) for row in rows]

    # -- writing ---------------------------------------------------------------------
    def write(self, question: str, answer: str, evidence: Sequence[Dict[str, Any]] = (),
              context: Optional[Dict[str, Any]] = None, principles: Sequence[str] = (),
              conditions: str = "", topics: Sequence[Any] = (),
              written_by: Optional[Dict[str, Any]] = None,
              _lineage: str = "", _version: int = 1, _supersedes: str = "") -> Dict[str, Any]:
        """A new card, as a candidate - or why it is not kept: no question or
        no answer, or it rests on web content (used in its run only)."""
        question = " ".join(str(question or "").split())[:600]
        answer = str(answer or "").strip()[:4000]
        if not question or not answer:
            return {"kept": False, "why": "a card needs a question and its answer"}
        kept_evidence = []
        for one in evidence or []:
            kind = str((one or {}).get("type") or "")
            if kind not in EVIDENCE_TYPES:
                kind = "model"
            kept_evidence.append({"type": kind, "ref": str(one.get("ref") or "")[:500],
                                  "said": str(one.get("said") or "")[:1000]})
        tier = tier_of(kept_evidence)
        if tier == "web" or any(one["type"] == "web" for one in kept_evidence):
            return {"kept": False, "why": "it rests on web content: used in this run only, "
                                          "never kept"}
        context = dict(context or {})
        card_id = f"lc_{uuid.uuid4().hex[:16]}"
        at = _now()
        self.db.execute(
            "INSERT INTO lesson_cards (id, scope, lineage, version, supersedes, status, "
            "question, answer, context, evidence, tier, principles, conditions, facets, "
            "topics, written_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (card_id, self.scope, _lineage or card_id, _version, _supersedes, question,
             answer, json.dumps(context, ensure_ascii=False),
             json.dumps(kept_evidence, ensure_ascii=False), tier,
             json.dumps([str(one) for one in principles or []]), str(conditions or "")[:1000],
             json.dumps(facets_of(kept_evidence, str(context.get("project") or "")),
                        ensure_ascii=False),
             json.dumps(topics_of(topics), ensure_ascii=False),
             json.dumps(dict(written_by or {}), ensure_ascii=False), at, at))
        return {"kept": True, "card": self.get(card_id)}

    def supersede(self, card_id: str, **changes: Any) -> Dict[str, Any]:
        """A card changed: a NEW VERSION with the changes, the old one marked
        superseded - never edited."""
        old = self.get(card_id)
        if old is None:
            return {"kept": False, "why": "no such card"}
        if old["status"] not in FOUND:
            return {"kept": False, "why": f"that version is {old['status']}"}
        said = self.write(
            changes.get("question", old["question"]), changes.get("answer", old["answer"]),
            changes.get("evidence", old["evidence"]), changes.get("context", old["context"]),
            changes.get("principles", old["principles"]),
            changes.get("conditions", old["conditions"]), changes.get("topics", old["topics"]),
            changes.get("written_by", old["written_by"]),
            _lineage=old["lineage"], _version=int(old["version"]) + 1, _supersedes=old["id"])
        if said.get("kept"):
            self.db.execute("UPDATE lesson_cards SET status = 'superseded', updated_at = ? "
                            "WHERE id = ?", (_now(), card_id))
        return said

    def mark(self, card_id: str, status: str) -> bool:
        """Kept, or retired - the state alone; the card's words never change."""
        if status not in ("kept", "retired"):
            return False
        card = self.get(card_id)
        if card is None or card["status"] not in FOUND:
            return False
        self.db.execute("UPDATE lesson_cards SET status = ?, updated_at = ? WHERE id = ?",
                        (status, _now(), card_id))
        return True

    def used(self, card_id: str, run_id: str = "", work_card: str = "",
             decision: str = "", files: Sequence[str] = ()) -> None:
        """A card used: by which run, on which card of its work, to what
        decision, touching which files."""
        self.db.execute(
            "INSERT INTO lesson_card_uses (card_id, run_id, work_card, decision, files, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (card_id, run_id, work_card, str(decision or "")[:1000],
             json.dumps([str(one) for one in files or []][:50], ensure_ascii=False), _now()))

    def uses(self, card_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM lesson_card_uses WHERE card_id = ? ORDER BY id",
                             (card_id,))
        return [dict(row, files=_loads(row["files"], [])) for row in rows]

    # -- finding ---------------------------------------------------------------------
    def find(self, question: str, project: str = "", limit: int = 5) -> List[Dict[str, Any]]:
        """The cards whose QUESTION is most like this one - each of its words
        worth what it is rare among the questions kept - a card from the same
        project counting for more; each with why it was found."""
        asked = _terms(question)
        if not asked:
            return []
        cards = self.cards()
        if not cards:
            return []
        terms = {card["id"]: set(_terms(card["question"])) for card in cards}
        size = len(cards)
        rarity = {w: math.log(1.0 + size / max(1, sum(1 for t in terms.values() if w in t)))
                  for w in asked}
        whole = sum(rarity.values()) or 1.0
        ranked = []
        for card in cards:
            shared = [w for w in asked if w in terms[card["id"]]]
            share = sum(rarity[w] for w in shared) / whole
            if share < LEAST_SHARE:
                continue
            same = bool(project) and str(card["context"].get("project") or "") == project
            score = share * (1.0 + (SAME_PROJECT if same else 0.0))
            why = ["question: " + ", ".join(shared[:6])]
            if same:
                why.append("same project")
            ranked.append((score, card["created_at"], card, why))
        ranked.sort(key=lambda one: (one[0], one[1]), reverse=True)
        return [dict(card, score=round(score, 3), why=why)
                for score, _at, card, why in ranked[:limit]]

    def asked_before(self, question: str, project: str = "", limit: int = 10) -> List[str]:
        """Only the QUESTIONS asked before on work like this - cheap to give,
        and they prompt questions a reviewer would not think of."""
        return [card["question"] for card in self.find(question, project, limit)]


__all__ = ["LessonCards", "EVIDENCE_TYPES", "tier_of", "facets_of", "topics_of"]
