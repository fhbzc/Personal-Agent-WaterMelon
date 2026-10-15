"""Nature records in two kinds, and the patterns built before there were two.

WORK nature is the user's taste and standards in work, and how they like to
work together: what an agent plans and organises their work by. PERSONAL
nature is habits, rhythm and interests: for a companion, later. Step 2 of
the agent heartbeat says which kind each new record is; the records written
before it did are sorted here, once, a batch to a call - and the patterns
built from them before there were kinds are given the kind most of their
records have, so the words already written for them are kept.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence

from .store import NATURE_KINDS, NATURE_SAID, ExperienceStore, now

log = logging.getLogger(__name__)

#: Records sorted in one call.
SORT_BATCH = 50

SORT_PROMPT = """SORT THESE OBSERVATIONS ABOUT THE USER INTO TWO KINDS.

Each item below is something an agent concluded about the user, with its
id. Say for each which kind it is:

  - "work": their TASTE AND STANDARDS IN WORK, and how they like to work
    together - what a good result looks like to them in slides, writing,
    code, interfaces, figures or mail; what they accept and send back, and
    why; how they check a result; whether they want a proposal before a
    change, how much the agent should decide alone, how they want progress
    reported. The test: would an agent about to plan or do a piece of their
    work do something differently because of it?
  - "personal": WHO THEY ARE outside the results of their work - habits and
    rhythms (when they work, rest, sleep; how they move through a day and
    switch between things, even while working), hobbies and interests, what
    they care about, how they unwind.

  - "both": it is genuinely both - it says what their work should look
    like or how to work with them AND who they are. "The user works a
    design out in conversation before any code is touched" is how an agent
    should plan with them, and how they think.

A habit at work is personal: "the user checks running jobs in brief
glances" is how they move through a day, not what their work should look
like. Say "both" only when it truly is both, not to avoid choosing.

Return ONLY this JSON object, with every id given:
{"kinds": {"<id>": "work, personal or both"}}"""


def kind_of(record: Any, kinds: Dict[str, str]) -> str:
    """A record's kind as said - work, personal or both - as sorted, or as
    step 2 wrote it; '' when not known."""
    if record.id in kinds:
        return kinds[record.id]
    said = str((getattr(record, "metadata", None) or {}).get("nature_kind") or "")
    return said if said in NATURE_SAID else ""


def kinds_of(record: Any, kinds: Dict[str, str]) -> set:
    """The kinds a record is grouped in: both of them, for one that is both."""
    said = kind_of(record, kinds)
    return set(NATURE_KINDS) if said == "both" else ({said} if said else set())


def is_kind(said: str, kind: str) -> bool:
    """Whether a record said to be `said` belongs with `kind`."""
    return said == kind or (said == "both" and kind in NATURE_KINDS)


class NatureSorter:
    """Gives every nature record a kind, and the old patterns theirs."""

    def __init__(self, db: Any, models: Any, scope: str,
                 ask: Callable[[str, str, int], Any]) -> None:
        self.store = ExperienceStore(db, scope)
        self.models = models
        self._ask = ask

    def unsorted(self, records: Sequence[Any]) -> List[Any]:
        """The records whose kind is not known - not sorted, and not written
        with one by step 2."""
        kinds = self.store.nature_kinds()
        return [record for record in records if not kind_of(record, kinds)]

    def sort(self, records: Sequence[Any], budget: int,
             going: Optional[Callable[[], bool]] = None,
             progress: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Sort what has no kind yet - a batch to a call, each saved at once."""
        going = going or (lambda: True)
        progress = progress if progress is not None else {}
        asked = self.unsorted(records)
        report = {"calls": 0, "sorted": 0, "waiting": len(asked)}
        progress.update(sorting=len(asked), sorted=0)
        for start in range(0, len(asked), SORT_BATCH):
            if report["calls"] >= budget or not going():
                break
            batch = asked[start:start + SORT_BATCH]
            lines = "\n".join(f"- [{r.id}] {r.title}: {(r.summary or '')[:240]}"
                              for r in batch)
            answered, writer = self._ask(SORT_PROMPT, lines, 3000)
            report["calls"] += 1
            given = answered.get("kinds") if isinstance(answered, dict) else None
            for record in batch:
                kind = str((given or {}).get(record.id) or "").strip().lower()
                if kind in NATURE_SAID:
                    self.store.set_nature_kind(record.id, kind, writer)
                    report["sorted"] += 1
                    progress["sorted"] = progress.get("sorted", 0) + 1
        report["left"] = len(self.unsorted(records))
        return report

    def carry_over(self, records: Sequence[Any] = ()) -> Dict[str, int]:
        """The patterns and principles built before there were kinds, each
        given the kind most of what is under it has. What is under it of the
        other kind leaves it, and is placed among its own kind as anything
        new is.

        A record's kind as sorted, or as step 2 wrote it into the record -
        both, so one that came with its kind counts as known."""
        kinds = dict(self.store.nature_kinds())
        for record in records:
            said = kind_of(record, kinds)
            if said:
                kinds[record.id] = said
        moved = {"patterns": 0, "principles": 0, "left": 0}
        old_patterns = self.store.patterns("pattern", kind="")
        pattern_kind: Dict[str, str] = {}
        for pattern in old_patterns:
            counted = Counter(kind for one in pattern["members"]
                              if one in kinds
                              for kind in (NATURE_KINDS if kinds[one] == "both"
                                           else (kinds[one],)))
            if not counted:
                continue
            kind = counted.most_common(1)[0][0]
            # A record that is both stays, and is grouped in the other kind
            # as well, as anything new is.
            members = [one for one in pattern["members"]
                       if is_kind(kinds.get(one, ""), kind)]
            moved["left"] += len(pattern["members"]) - len(members)
            pattern.update(nature_kind=kind, members=members,
                           support=len(members))
            # Fewer under it than were worded: its words still stand, and
            # are worded again only once it grows by half as usual.
            self.store.save_pattern(pattern)
            pattern_kind[pattern["id"]] = kind
            moved["patterns"] += 1
        for pattern in self.store.patterns("pattern"):
            pattern_kind.setdefault(pattern["id"],
                                    pattern.get("nature_kind") or "")
        by_id = {p["id"]: p for p in self.store.patterns("pattern")}
        for principle in self.store.patterns("principle", kind=""):
            counted = Counter(pattern_kind[one] for one in principle["members"]
                              if pattern_kind.get(one))
            if not counted:
                continue
            kind = counted.most_common(1)[0][0]
            members = [one for one in principle["members"]
                       if pattern_kind.get(one) == kind]
            for one in principle["members"]:
                if one not in members and one in by_id:
                    # Left for its own kind's principles to take in.
                    by_id[one]["parent"] = ""
                    self.store.save_pattern(by_id[one])
            principle.update(nature_kind=kind, members=members,
                             support=sum(len(by_id[one]["members"])
                                         for one in members if one in by_id))
            self.store.save_pattern(principle)
            moved["principles"] += 1
        return moved
