"""THE TOPICS OF WHAT THE USER READS - what they care about, and how much.

Each piece of the reading log (log.py) is embedded - its summary and its
topics, in English - and placed in the topic whose centre it is nearest, if
near enough (`PLACED_AT`); else it starts a topic of its own, named after
its first topic. A topic's centre moves with each piece placed in it.

How much a topic matters now - its WEIGHT - is not kept, it is worked out:
each of its pieces counts for how much the user engaged with it, halving
every `HALF_LIFE_DAYS` since it was last read. A topic read much and long
ago weighs less than one read a little today.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

#: How near a piece must be to a topic's centre to be placed in it.
PLACED_AT = 0.55
#: A piece's weight halves every this many days since it was last read.
HALF_LIFE_DAYS = 14.0
#: Pieces embedded in one go.
EMBED_BATCH = 64


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    size = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return 0.0 if size <= 0 else max(-1.0, min(1.0, dot / size))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_days(at: Any, now: datetime) -> float:
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (now - when).total_seconds() / 86400.0)


def decayed(engagement: float, last_at: Any, now: Optional[datetime] = None) -> float:
    return float(engagement or 0.0) * 0.5 ** (_age_days(last_at, now or _now())
                                              / HALF_LIFE_DAYS)


def piece_text(row: Dict[str, Any]) -> str:
    """What a piece is embedded as: its sentence and its topics."""
    topics = row["topics"] if isinstance(row["topics"], list) else json.loads(
        row["topics"] or "[]")
    return (str(row["summary"] or row["title"] or "") + " | " + ", ".join(topics)).strip()


class TopicModel:
    """The topics, their centres, and the pieces placed in them."""

    def __init__(self, db: Any, models: Any = None) -> None:
        self.db = db
        self.models = models

    def place_waiting(self, embedder: Any) -> Dict[str, int]:
        """Every piece not placed yet: embedded, and placed."""
        rows = [dict(row) for row in self.db.query(
            "SELECT * FROM reading_pieces WHERE topic_id = '' ORDER BY first_at")]
        if not rows or embedder is None:
            return {"placed": 0, "made": 0}
        placed = made = 0
        for start in range(0, len(rows), EMBED_BATCH):
            part = rows[start:start + EMBED_BATCH]
            try:
                vectors = embedder.embed([piece_text(row) for row in part])
            except Exception:                    # pragma: no cover - placed next time
                log.warning("the reading log could not be embedded", exc_info=True)
                break
            if not vectors or len(vectors) != len(part):
                break
            for row, vector in zip(part, vectors):
                vector = [round(float(x), 5) for x in vector]
                topic_id, new = self.place(row, vector)
                placed += 1
                made += int(new)
        return {"placed": placed, "made": made}

    def place(self, row: Dict[str, Any], vector: List[float]):
        """One piece, into the topic it is nearest - or a topic of its own."""
        from ...common.events.models import new_id
        best, best_score = None, 0.0
        for topic in self.db.query("SELECT id, centre, pieces FROM reading_topics"):
            score = cosine(vector, json.loads(topic["centre"] or "[]"))
            if score > best_score:
                best, best_score = dict(topic), score
        at = _now().isoformat()
        if best is not None and best_score >= PLACED_AT:
            n = int(best["pieces"] or 0)
            centre = json.loads(best["centre"])
            centre = [round((c * n + v) / (n + 1), 5) for c, v in zip(centre, vector)]
            self.db.execute("UPDATE reading_topics SET centre = ?, pieces = ?, updated_at = ? "
                            "WHERE id = ?", (json.dumps(centre), n + 1, at, best["id"]))
            topic_id, new = best["id"], False
        else:
            topics = row["topics"] if isinstance(row["topics"], list) else json.loads(
                row["topics"] or "[]")
            topic_id, new = new_id("topic"), True
            self.db.execute("INSERT INTO reading_topics (id, label, centre, pieces, created_at, "
                            "updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                            (topic_id, (topics[0] if topics else row["title"] or "")[:80],
                             json.dumps(vector), at, at))
        self.db.execute("UPDATE reading_pieces SET topic_id = ?, vector = ? WHERE id = ?",
                        (topic_id, json.dumps(vector), row["id"]))
        return topic_id, new

    def weights(self, now: Optional[datetime] = None) -> Dict[str, float]:
        """Each topic's weight now (module docstring)."""
        now = now or _now()
        out: Dict[str, float] = {}
        for row in self.db.query("SELECT topic_id, engagement, last_at FROM reading_pieces "
                                 "WHERE topic_id != ''"):
            out[row["topic_id"]] = out.get(row["topic_id"], 0.0) + decayed(
                row["engagement"], row["last_at"], now)
        return {key: round(value, 4) for key, value in out.items()}

    def describe(self) -> List[Dict[str, Any]]:
        """The topics, the weightiest first, each with its latest pieces."""
        weights = self.weights()
        out = []
        for topic in self.db.query("SELECT id, label, pieces, updated_at FROM reading_topics"):
            latest = [row["title"] for row in self.db.query(
                "SELECT title FROM reading_pieces WHERE topic_id = ? ORDER BY last_at DESC "
                "LIMIT 3", (topic["id"],))]
            out.append({"id": topic["id"], "label": topic["label"],
                        "pieces": int(topic["pieces"] or 0),
                        "weight": weights.get(topic["id"], 0.0),
                        "updated_at": topic["updated_at"], "latest": latest})
        out.sort(key=lambda one: -one["weight"])
        return out


__all__ = ["TopicModel", "cosine", "decayed", "PLACED_AT", "HALF_LIFE_DAYS"]
