"""HOW RARE A TRIGGER WORD IS FOR THIS USER (the user, 2026-11-06): an inverse document frequency of
their own, from what has been on their screens and what they read lately. A word they see all day
says little when it turns up again; one they rarely see says more.

  - A DOCUMENT is a window on one day, its lines once each, or one piece they read. Counted by the
    read, a page looked at fifty times would make its words common.
  - ONLY THE TRIGGER WORDS ARE COUNTED, as they are matched: no word list to keep, and Chinese needs
    no splitting into words.
  - A WEIGHT, NEVER NOTHING (`FLOOR`): what is kept ready is about their work, and the words of their
    work are the ones they see most. Weighed down to nothing, the most fitting words would count
    least.
  - Worked out in the heartbeat's step, a day apart, and when words are new. Matching reads the
    weights and counts nothing. How often a word was repeated on a page counts for nothing: it says
    how long the page is.

Screen text is kept as long as raw activity is (eight days by default), what was read longer.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

#: How far back it looks, what a word weighs at the least, and how few documents say nothing.
DAYS, FLOOR, LEAST_DOCUMENTS = 14, 0.3, 50
#: How often it is worked out again; with new words, how soon.
EVERY_HOURS, NEW_WORDS_HOURS = 24, 1
COUNTED_AT, COUNTED_OF = "reserve.rarity_at", "reserve.rarity_documents"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reserve_word_rarity (
    word   TEXT PRIMARY KEY,
    seen   INTEGER NOT NULL DEFAULT 0,
    weight REAL NOT NULL DEFAULT 1
);
"""


def documents(db: Any, since: str) -> List[str]:
    """What the user saw and read since then, each document folded as words are matched."""
    from ..core.triggers import window_key
    from .match import folded
    windows: Dict[Tuple[str, str], Dict[str, None]] = {}
    for row in db.query("SELECT timestamp, application, data FROM raw_events WHERE event_type = "
                        "'screen.text' AND timestamp >= ?", (since,)):
        try:
            data = json.loads(row["data"] or "{}")
        except ValueError:
            continue
        key = (window_key(str(row["application"] or ""), data.get("handle"),
                          str(data.get("window_title") or "")), str(row["timestamp"])[:10])
        windows.setdefault(key, {}).update(dict.fromkeys(str(data.get("text") or "").splitlines()))
    out = [folded("\n".join(lines)) for lines in windows.values()]
    for row in db.query("SELECT title, summary, points, topics, entities FROM reading_pieces "
                        "WHERE last_at >= ?", (since,)):
        out.append(folded(" ".join(str(row[key] or "") for key in row.keys())))
    return [one for one in out if one]


def seen_in(word: str, texts: Iterable[str]) -> int:
    from .match import found
    needle = word.casefold()
    return sum(1 for one in texts if needle in one and found(word, one))


def weight_of(seen: int, total: int) -> float:
    """1 for a word never seen, down to FLOOR for one in everything."""
    if total < LEAST_DOCUMENTS:
        return 1.0
    return round(max(FLOOR, min(1.0, math.log((total + 1) / (seen + 1)) / math.log(total + 1))), 3)


class Rarity:
    def __init__(self, db: Any) -> None:
        self.db = db
        with db.write() as conn:
            conn.executescript(SCHEMA)

    def _kept(self, key: str) -> str:
        row = self.db.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return str(row["value"] or "") if row is not None else ""

    def _keep(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                        "value = excluded.value", (key, value))

    def weights(self) -> Dict[str, float]:
        """Each word's weight, by the word as it is folded."""
        return {row["word"]: float(row["weight"]) for row in self.db.query(
            "SELECT word, weight FROM reserve_word_rarity")}

    def described(self) -> Dict[str, Any]:
        return {"at": self._kept(COUNTED_AT), "documents": int(self._kept(COUNTED_OF) or 0),
                "weights": self.weights()}

    def due(self, words: Iterable[str], now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        words = {one.casefold() for one in words}
        if not words:
            return False
        at = self._kept(COUNTED_AT)
        if not at:
            return True
        since = now - datetime.fromisoformat(at)
        if since >= timedelta(hours=EVERY_HOURS):
            return True
        return since >= timedelta(hours=NEW_WORDS_HOURS) and bool(words - set(self.weights()))

    def refresh(self, words: Iterable[str], now: Optional[datetime] = None) -> int:
        """Every word counted again. How many documents there were."""
        now = now or datetime.now(timezone.utc)
        texts = documents(self.db, (now - timedelta(days=DAYS)).isoformat())
        total = len(texts)
        rows = []
        for word in sorted({one.casefold() for one in words}):
            seen = seen_in(word, texts)
            rows.append((word, seen, weight_of(seen, total)))
        with self.db.write() as conn:
            conn.execute("DELETE FROM reserve_word_rarity")
            conn.executemany("INSERT INTO reserve_word_rarity (word, seen, weight) VALUES (?, ?, ?)",
                             rows)
        self._keep(COUNTED_AT, now.isoformat())
        self._keep(COUNTED_OF, str(total))
        return total


__all__ = ["Rarity", "documents", "seen_in", "weight_of", "DAYS", "FLOOR", "LEAST_DOCUMENTS"]
