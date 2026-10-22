"""THE SHARED WORDS: what cases, judgments, reasons and jobs are tagged with.

Each dimension has a fixed top - written here, never changed by a model -
and what is under it is added by the model as it is needed: a term it adds
is on TRIAL until a second case uses it, and one that never gets there is
folded back into its parent - but only after long enough (`TRIAL_DAYS`) AND
enough chances (`TRIAL_CHANCES` judgments tagged in its branch), and never
while a place is being read for the first time. Two terms that mean one
thing are merged: the old one stays as an ALIAS, so every tag written with
it still reads. Tags are kept as term ids and read through the aliases, so
nothing is rewritten when terms move.

Matching is by these words, never by keywords or meaning: "the same
object", "under the same parent", "a sibling" (`relation`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: The dimensions a case, a reason's condition and a job are tagged in.
DIMENSIONS = ("deliverable", "audience", "presentation", "purpose", "environment")
#: What a judgment is about in the output - its own dimension.
OBJECT = "object"
#: What kind of thing a judgment or reason says.
ASPECTS = ("requirement", "output", "evaluation", "habit")
ASPECT_WORDS = {
    "requirement": "something the work must also have or do",
    "output": "the form, content or style of the output",
    "evaluation": "how a result is checked and judged",
    "habit": "how the work is done: steps, tools, working habits",
}

#: THE FIXED TOP of each dimension: term -> its children (also fixed).
FIXED: Dict[str, Dict[str, Sequence[str]]] = {
    "deliverable": {
        "document": ("paper", "report", "proposal", "letter", "notes", "handout",
                     "review", "thesis"),
        "visual material": ("slides", "poster", "figure", "chart", "video", "diagram"),
        "message": ("email", "chat message", "post"),
        "code": ("web app", "script", "library", "notebook", "interface", "tests"),
        "data": ("table", "dataset", "analysis"),
        "form": (),
        "meeting": (),
        "setup": (),
        "other deliverable": (),
    },
    "audience": {
        "self": (), "collaborators": (), "colleagues": (), "peers": (),
        "reviewers": (), "supervisors": (), "students": (), "public": (),
        "clients": (), "machines": (),
    },
    "presentation": {
        "presented in person": (), "read alone": (), "used interactively": (),
        "recorded": (), "sent as a message": (), "run by a machine": (),
    },
    "purpose": {
        "persuade or defend": (), "inform": (), "examine together": (),
        "teach": (), "decide": (), "record": (), "build a tool": (),
        "explore": (),
    },
    "environment": {
        "writing tool": (), "presentation tool": (), "programming language": (),
        "data tool": (), "platform or service": (), "operating system": (),
        "other tool": (),
    },
    "object": {
        "amount of text": (), "structure and order": (), "look and layout": (),
        "colour": (), "typography": (), "figures and tables": (),
        "wording and tone": (), "length": (), "references and citations": (),
        "numbers and units": (), "names and files": (), "what is covered": (),
        "correctness and checks": (), "steps of the work": (), "tools used": (),
        "delivery and sharing": (), "privacy and data": (), "timing": (),
    },
}

#: How deep a term may be added below the top (the top is depth 0).
MOST_DEPTH = 3
#: A trial term is folded back into its parent only after this long...
TRIAL_DAYS = 90
#: ...and this many judgments tagged in its branch since it was added.
TRIAL_CHANCES = 100
#: A trial term is kept once this many cases use it.
KEPT_AT_CASES = 2

LIVE = ("fixed", "trial", "kept")


def clean(term: Any) -> str:
    """A term as it is kept: lower case, single spaces, no punctuation at
    the ends."""
    text = re.sub(r"\s+", " ", str(term or "")).strip().strip(".,;:!?\"'").lower()
    return text[:60]


def term_id(dim: str, term: str) -> str:
    return f"{dim}:{clean(term)}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Vocabulary:
    """The terms, in the database (learn_terms)."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self._fixed_ready = False

    # -- the fixed top --------------------------------------------------------
    def ensure_fixed(self) -> None:
        if self._fixed_ready:
            return
        rows = []
        at = _now()
        for dim, tops in FIXED.items():
            for top, children in tops.items():
                rows.append((term_id(dim, top), dim, clean(top), "", "fixed", at))
                for child in children:
                    rows.append((term_id(dim, child), dim, clean(child),
                                 term_id(dim, top), "fixed", at))
        self.db.executemany(
            "INSERT OR IGNORE INTO learn_terms (id, dim, term, parent, state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", rows)
        self._fixed_ready = True

    # -- reading --------------------------------------------------------------
    def all(self, dims: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        self.ensure_fixed()
        rows = self.db.query("SELECT * FROM learn_terms")
        wanted = set(dims) if dims else None
        return [row for row in rows if wanted is None or row["dim"] in wanted]

    def by_id(self) -> Dict[str, Dict[str, Any]]:
        return {row["id"]: row for row in self.all()}

    def resolve(self, tag: Any, terms: Optional[Dict[str, Dict[str, Any]]] = None
                ) -> str:
        """A tag as it reads now: through its aliases; "" when unknown."""
        terms = terms if terms is not None else self.by_id()
        tag = str(tag or "")
        seen = set()
        while tag in terms and terms[tag]["state"] == "alias" and tag not in seen:
            seen.add(tag)
            tag = terms[tag]["alias_of"]
        return tag if tag in terms else ""

    def ancestors(self, tag: str, terms: Optional[Dict[str, Dict[str, Any]]] = None
                  ) -> List[str]:
        """The tag and every term above it, nearest first."""
        terms = terms if terms is not None else self.by_id()
        out: List[str] = []
        tag = self.resolve(tag, terms)
        while tag and tag not in out:
            out.append(tag)
            tag = self.resolve(terms[tag]["parent"], terms) if terms[tag]["parent"] else ""
        return out

    def depth(self, tag: str, terms: Optional[Dict[str, Dict[str, Any]]] = None) -> int:
        return max(0, len(self.ancestors(tag, terms)) - 1)

    def relation(self, one: str, other: str,
                 terms: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
        """How two tags stand: "same", "under" (one below other), "over",
        "sibling" (one parent), or ""."""
        terms = terms if terms is not None else self.by_id()
        a, b = self.ancestors(one, terms), self.ancestors(other, terms)
        if not a or not b:
            return ""
        if a[0] == b[0]:
            return "same"
        if b[0] in a:
            return "under"
        if a[0] in b:
            return "over"
        if len(a) > 1 and len(b) > 1 and a[1] == b[1]:
            return "sibling"
        return ""

    def common(self, tags: Sequence[str],
               terms: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
        """The nearest term every tag is at or under; "" when none."""
        terms = terms if terms is not None else self.by_id()
        chains = [self.ancestors(tag, terms) for tag in tags if tag]
        chains = [chain for chain in chains if chain]
        if not chains:
            return ""
        for candidate in chains[0]:
            if all(candidate in chain for chain in chains[1:]):
                return candidate
        return ""

    # -- what a model is shown ------------------------------------------------
    def listing(self, dims: Iterable[str]) -> str:
        """The live terms of these dimensions as an indented tree, one per
        line, with their id - what a model chooses from."""
        terms = self.by_id()
        lines: List[str] = []
        for dim in dims:
            mine = [row for row in terms.values() if row["dim"] == dim
                    and row["state"] in LIVE]
            children: Dict[str, List[Dict[str, Any]]] = {}
            for row in mine:
                children.setdefault(row["parent"], []).append(row)

            def walk(parent: str, depth: int) -> None:
                for row in sorted(children.get(parent, []), key=lambda r: r["term"]):
                    trial = " (trial)" if row["state"] == "trial" else ""
                    lines.append("  " * depth + f"- {row['id']}{trial}")
                    walk(row["id"], depth + 1)

            lines.append(f"{dim.upper()}:")
            walk("", 1)
        return "\n".join(lines)

    @staticmethod
    def fixed_listing(dims: Iterable[str]) -> str:
        """The fixed top only - nothing added from the user's work: for a
        reviewer that is to be given none of it."""
        lines: List[str] = []
        for dim in dims:
            lines.append(f"{dim.upper()}:")
            for top, children in FIXED.get(dim, {}).items():
                lines.append(f"  - {term_id(dim, top)}")
                lines += [f"    - {term_id(dim, child)}" for child in children]
        return "\n".join(lines)

    # -- adding ---------------------------------------------------------------
    def add(self, dim: str, term: str, parent: str, definition: str = "",
            example: str = "") -> str:
        """A term the model added under `parent`, on trial; its id. Refused
        ("") with no live parent in the same dimension, or too deep."""
        terms = self.by_id()
        parent = self.resolve(parent, terms)
        name = clean(term)
        if not name or dim not in FIXED or not parent or terms[parent]["dim"] != dim:
            return ""
        if self.depth(parent, terms) + 1 > MOST_DEPTH:
            return parent
        tag = term_id(dim, name)
        if tag in terms:
            return self.resolve(tag, terms) or tag
        branch = self.branch_judgments(parent)
        self.db.execute(
            "INSERT OR IGNORE INTO learn_terms (id, dim, term, parent, definition, example, "
            "state, branch_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 'trial', ?, ?)",
            (tag, dim, name, parent, str(definition or "")[:300],
             str(example or "")[:300], branch, _now()))
        return tag

    def used(self, tags: Iterable[str], case_id: str) -> None:
        """These tags were used for a case: a trial term two cases use is
        kept."""
        terms = self.by_id()
        for tag in {self.resolve(tag, terms) for tag in tags if tag}:
            if not tag or terms[tag]["state"] != "trial":
                continue
            cases = set(json.loads(terms[tag]["cases"] or "[]"))
            cases.add(case_id)
            state = "kept" if len(cases) >= KEPT_AT_CASES else "trial"
            self.db.execute("UPDATE learn_terms SET cases = ?, state = ? WHERE id = ?",
                            (json.dumps(sorted(cases)), state, tag))

    def branch_judgments(self, tag: str) -> int:
        """How many judgments are tagged at or under `tag` (object terms) -
        or how many cases are (the other dimensions)."""
        terms = self.by_id()
        below = {one for one in terms if tag in self.ancestors(one, terms)}
        if not below:
            return 0
        holes = ",".join("?" for _ in below)
        if tag.startswith(OBJECT + ":"):
            row = self.db.query_one(
                f"SELECT COUNT(*) AS n FROM learn_judgments WHERE object IN ({holes})",
                tuple(below))
            return int(row["n"]) if row else 0
        count = 0
        for row in self.db.query("SELECT facets FROM learn_cases WHERE alias_of = ''"):
            facets = json.loads(row["facets"] or "{}")
            if any(one in below for values in facets.values() for one in values or []):
                count += 1
        return count

    # -- upkeep ---------------------------------------------------------------
    def prune(self, first_learning: bool) -> List[str]:
        """Trial terms that had their time and their chances and were never
        used by a second case, folded into their parents (as aliases).
        Nothing while a place is read for the first time."""
        if first_learning:
            return []
        folded = []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=TRIAL_DAYS)).isoformat()
        for row in self.db.query("SELECT * FROM learn_terms WHERE state = 'trial' "
                                 "AND created_at < ?", (cutoff,)):
            chances = self.branch_judgments(row["parent"]) - int(row["branch_at"] or 0)
            if chances < TRIAL_CHANCES:
                continue
            self.merge(row["id"], row["parent"])
            folded.append(row["id"])
        return folded

    def merge(self, gone: str, into: str) -> bool:
        """`gone` reads as `into` from now on; never deleted."""
        terms = self.by_id()
        gone, into = self.resolve(gone, terms), self.resolve(into, terms)
        if not gone or not into or gone == into or terms[gone]["state"] == "fixed":
            return False
        # Folding into a term above is fine; into one below would loop.
        if gone in self.ancestors(into, terms):
            return False
        self.db.execute("UPDATE learn_terms SET state = 'alias', alias_of = ? WHERE id = ?",
                        (into, gone))
        # Its children move up to what it became.
        self.db.execute("UPDATE learn_terms SET parent = ? WHERE parent = ?", (into, gone))
        return True

    def children(self, parent: str) -> List[Dict[str, Any]]:
        return [row for row in self.all() if row["parent"] == parent
                and row["state"] in LIVE]
