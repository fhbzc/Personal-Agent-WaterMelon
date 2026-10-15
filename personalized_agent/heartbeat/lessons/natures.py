"""THE NATURES OF THE USER'S WORK, LEARNED AS LESSONS ARE (engine.py).

A nature is formed from a stage of the user's work when the stage is
written (experience/stage_natures.py) - a stage is a whole episode, so one
is enough to form it. From then on it is kept the way every lesson is: the
stages it was written from are the evidence for it; each new stage is
matched against the natures it may bear on, and supports or contradicts
them; its confidence rises and falls with that; a contradicted nature is
narrowed, or retired; and what it is - in force or still forming, how sure
- is written back onto it, for the reviewer to weigh.

The natures stay where they are, in the library, with their patterns and
principles: the engine keeps a rule for each (the same id), and its links.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

log = logging.getLogger(__name__)

#: Set once the natures were first brought in: stages written after it are
#: matched against them; the ones before were already read for theirs.
SINCE = "lessons_work_since"

#: A nature's words that make its conditions.
MOST_KEYWORDS = 12

_STOP = set("the and for that with this from they their them user users when what "
            "which into about have has are was were will would should not but also "
            "its it's each every more most than then".split())


def keywords(*texts: Any) -> List[str]:
    counts: Dict[str, int] = {}
    for text in texts:
        for word in re.findall(r"[a-z][a-z0-9-]{2,}", str(text or "").lower()):
            if word not in _STOP:
                counts[word] = counts.get(word, 0) + 1
    return [word for word, _n in sorted(counts.items(), key=lambda one: -one[1])][:MOST_KEYWORDS]


def stage_text(stage: Dict[str, Any]) -> str:
    """A stage, as evidence: what it was, what was decided and judged."""
    work = stage.get("work") or {}
    decision = work.get("decision") or {}
    evaluation = stage.get("evaluation") or {}
    lines = [f"A {stage.get('kind') or 'stage'} of the user's work: {stage.get('summary') or ''}"]
    if decision.get("chose"):
        lines.append(f"Decided: {decision.get('chose')}"
                     + (f" - because {decision['why']}" if decision.get("why") else ""))
    if evaluation.get("verdict"):
        lines.append(f"Judged: {evaluation.get('verdict')}"
                     + (f" - {'; '.join(map(str, evaluation.get('words') or []))}"
                        if evaluation.get("words") else ""))
    return "\n".join(lines)[:2000]


class NatureLessons:
    """The work's natures, brought into the lessons engine and written back."""

    def __init__(self, db: Any, library: Any, config: Any = None) -> None:
        self.db = db
        self.library = library
        self.config = config

    def _natures(self) -> List[Any]:
        """Every nature of the work kind - retired ones too, so their state
        is kept."""
        from ..experience.natures import kinds_of
        from ..experience.patterns import folder_records, LEFT_OUT
        from ..experience.store import ExperienceStore
        kinds = ExperienceStore(self.db).nature_kinds()
        return [record for record in folder_records(self.library, "user_nature")
                if int(getattr(record, "level", 0) or 0) == 0
                and str(getattr(record, "source", "") or "") not in LEFT_OUT
                and "work" in kinds_of(record, kinds)]

    def due(self, engine: Any) -> bool:
        """A nature not brought in yet, or a link of one not."""
        known = {row["id"] for row in self.db.query(
            "SELECT id FROM lesson_rules WHERE kind = 'work'")}
        if any(record.id not in known for record in self._natures()):
            return True
        missing = self.db.query_one(
            "SELECT COUNT(*) AS n FROM stage_natures s WHERE NOT EXISTS (SELECT 1 FROM "
            "lesson_links l JOIN lesson_evidence e ON e.id = l.evidence_id WHERE "
            "e.kind = 'work' AND e.source_id = s.stage_id AND l.rule_id = s.record_id)")
        return bool((missing or {}).get("n"))

    def sync(self, engine: Any) -> Dict[str, int]:
        """Each nature as a rule (its id, its words); the stages it was
        written from as its evidence; and each stage written since the last
        time, as new evidence to match against the natures."""
        from ...common.events.models import new_id
        from ..experience.store import ExperienceStore
        from .engine import _iso
        store = ExperienceStore(self.db)
        records = self._natures()
        rules = {row["id"]: dict(row) for row in self.db.query(
            "SELECT * FROM lesson_rules WHERE kind = 'work'")}
        made = linked = fresh = 0
        for record in records:
            conditions = {"keywords": keywords(record.title, record.summary)}
            if record.id not in rules:
                at = _iso()
                self.db.execute(
                    "INSERT INTO lesson_rules (id, kind, statement, conditions, state, pinned, "
                    "support, contradict, confidence, created_at, updated_at, history) VALUES "
                    "(?, 'work', ?, ?, 'candidate', 0, 0, 0, 0.5, ?, ?, ?)",
                    (record.id, " ".join(str(record.title or "").split())[:600],
                     json.dumps(conditions, ensure_ascii=False), at, at,
                     json.dumps([{"at": at, "what": "made",
                                  "why": "a nature of the user's work, from its stages"}])))
                made += 1
        evidence = {row["source_id"]: row["id"] for row in self.db.query(
            "SELECT id, source_id FROM lesson_evidence WHERE kind = 'work'")}
        ids = {record.id for record in records}

        def evidence_of(stage_id: str, read: bool) -> str:
            if stage_id in evidence:
                return evidence[stage_id]
            stage = store.stage(stage_id) or {"summary": stage_id}
            ident = new_id("evi")
            self.db.execute(
                "INSERT INTO lesson_evidence (id, kind, source_id, at, action, summary, reason, "
                "weight, features, processed_at, outcome_of) VALUES (?, 'work', ?, ?, 'stage', "
                "?, '', 1.0, ?, ?, '')",
                (ident, stage_id, str(stage.get("ended") or stage.get("began") or _iso()),
                 stage_text(stage), "{}" if read else None, _iso() if read else None))
            evidence[stage_id] = ident
            return ident

        for row in self.db.query("SELECT stage_id, record_id FROM stage_natures"):
            if row["record_id"] not in ids:
                continue
            ident = evidence_of(row["stage_id"], read=True)
            if self.db.query_one("SELECT 1 FROM lesson_links WHERE evidence_id = ? AND "
                                 "rule_id = ?", (ident, row["record_id"])) is None:
                self.db.execute("INSERT INTO lesson_links (evidence_id, rule_id, relation, why, "
                                "at) VALUES (?, ?, 'supports', ?, ?)",
                                (ident, row["record_id"],
                                 "the nature was written from this stage", _iso()))
                linked += 1
        # The stages written since: matched against the natures they may
        # bear on. The first time, from now - the stages before were read
        # for their natures when they were written.
        since = str(self.config.get(SINCE, "") or "") if self.config is not None else ""
        if not since:
            if self.config is not None:
                self.config.set(SINCE, _iso(), internal=True)
        else:
            for stage in self.db.query("SELECT id FROM experience_stages WHERE scope = 'live' "
                                       "AND created_at > ?", (since,)):
                if stage["id"] not in evidence:
                    evidence_of(stage["id"], read=False)
                    fresh += 1
            if self.config is not None:
                self.config.set(SINCE, _iso(), internal=True)
        return {"natures": made, "links": linked, "stages": fresh}

    def write_back(self, engine: Any) -> int:
        """What was learned of each nature, onto it: in force or forming,
        retired or rejected, how sure, on how many stages - and its words,
        where the engine narrowed them."""
        written = 0
        records = {record.id: record for record in self._natures()}
        for rule in engine.rules("work", ("candidate", "active", "retired", "rejected")):
            record = records.get(rule["id"])
            if record is None:
                continue
            meta = dict(getattr(record, "metadata", None) or {})
            want = {"lesson_state": rule["state"],
                    "confidence": round(float(rule["confidence"]), 3),
                    "lesson_cases": engine._cases(rule["id"])}
            changed = any(str(meta.get(key)) != str(value) for key, value in want.items())
            reworded = (rule["statement"] and rule["statement"] != record.title
                        and any(str(one.get("why") or "").startswith(("narrowed", "the user"))
                                for one in rule["history"][-3:]))
            if not changed and not reworded:
                continue
            meta.update(want)
            record.metadata = meta
            if reworded:
                record.title = rule["statement"]
            try:
                self.library.put(record)
                written += 1
            except Exception:              # pragma: no cover - the next cycle tries again
                log.warning("could not write back nature %s", rule["id"], exc_info=True)
        return written


__all__ = ["NatureLessons", "keywords", "stage_text", "SINCE"]
