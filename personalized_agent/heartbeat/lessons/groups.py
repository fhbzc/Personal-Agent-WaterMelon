"""THE LESSONS GROUPED AS THE NATURES ARE: patterns, and principles above
them, by experience's own builder (experience/patterns.py) - the lessons in
force of one kind being the items, where the natures are for the work."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

from ..experience.patterns import PatternBuilder


class _Item:
    """A lesson, as the builder reads an item: an id, words, when."""

    def __init__(self, rule: dict) -> None:
        self.id = rule["id"]
        self.title = rule["statement"]
        self.summary = " ".join(rule["conditions"].get("keywords") or [])
        try:
            self.created_at = datetime.fromisoformat(str(rule["created_at"]))
        except ValueError:
            self.created_at = datetime.now(timezone.utc)
        self.metadata = {}


class LessonPatternBuilder(PatternBuilder):
    """Patterns and principles over one kind's lessons in force."""

    #: Fewer lessons than natures: principles from the first few patterns.
    principles_from = 3
    principles_fewest = 1
    principles_most = 6

    def __init__(self, engine: Any, db: Any, library: Any, embedder: Any, models: Any,
                 kind: str) -> None:
        super().__init__(db, library, embedder, models, kind=kind)
        self.engine = engine

    def _records(self, until: str = "") -> List[Any]:
        return [_Item(rule) for rule in self.engine.rules(self.kind, ("active",))]


__all__ = ["LessonPatternBuilder"]
