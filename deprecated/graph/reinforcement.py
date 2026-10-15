"""Learning from what was used (section 44.1).

A weight is the graph's only record of what has actually proved related, and
until now nothing ever wrote to it: it was set when an edge was made and never
touched again, while the traversal that depends on it sampled from numbers
nobody had revised. The graph could not get better at being walked.

So every answer is evidence. The nodes and connections the agent walked to
build it are remembered, and afterwards - in the data heartbeat, not in front
of the waiting user - they are judged against the answer they produced. What
helped is made more likely to be walked again; what did not is made less
likely, and the same pass shortens anything that has grown too long to be
worth its place in a future context window.

**Evidence accumulates; the weight is a bounded view of it.** Two numbers,
because they answer different questions. The evidence is a plain running sum -
how many times this connection has proved itself, minus how many times it has
not - and it is what is actually learned. The weight is that sum passed
through tanh, and it exists only so that sampling has something bounded to
work with: an unbounded weight would mean an edge used a hundred times is
picked every time, exploration becomes the only thing that ever finds anything
else, and the graph freezes.

Squashing on the way *out* rather than on the way *in* is the whole point. A
sum that is squashed before being stored is squashed again on the next update,
so the compression compounds, the weight stalls well short of its ceiling, and
- worse - the evidence is destroyed: forty confirmations become indistinguish-
able from ten. Keeping the sum intact means the record of what has been
learned stays exact, and the bounded number is derived from it whenever it is
needed.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from ...common.events.models import new_id

log = logging.getLogger(__name__)

#: The range weights are held in. Sampling adds a floor of its own, so the
#: effective range is a little wider than this at the bottom.
WEIGHT_MAX = 5.0

#: How far one answer may move a weight before squashing. Small on purpose:
#: one answer is weak evidence, and a hundred answers should decide this, not
#: the most recent one.
STEP_UP = 0.6
STEP_DOWN = 0.4

#: Content longer than this is a candidate for shortening. It is roughly the
#: point at which one memory crowds out the others in a gathered context.
LONG_CONTENT = 1200
LONG_SUMMARY = 400

#: How much of one answer's assembled context is kept (section 14.8).
CONTEXT_MAX = 200000

#: What is given up, in order, when the context will not fit. The trace goes
#: first - it is the largest by far, and what it records can largely be read
#: back off the graph. The assembled text is not in this list at all: section
#: 14.8 keeps it verbatim because "a reconstruction of the context is not the
#: context", so it is shortened rather than dropped, and only once everything
#: here has already gone.
CONTEXT_SHED_ORDER = ("trace", "states", "nodes")

#: Says that the text above it is a prefix. Without it a shortened context
#: would read as a complete one, which is the failure 14.8 exists to prevent.
TEXT_TRUNCATED_MARKER = "\n[truncated: the context was longer than this log holds]"

#: How many distinct unreadable rows are remembered, so that each is reported
#: once rather than once per read. A bound on the memory, not on how many rows
#: may be reported.
UNREADABLE_REPORTED_MAX = 512


def encode_context(context: Optional[Dict[str, Any]]) -> str:
    """One answer's assembled context, as JSON that fits *and* parses.

    The column has a budget, and the obvious way to respect it - serialise,
    then cut the string to length - severs a string literal mid-value and
    stores a document that can never be read back. It is not a lossy record
    but an unreadable one, and the loss is silent until something tries.

    So the shedding is done to the data, before serialising. What is stored is
    always a complete document: smaller than what was asked for when it has to
    be, and honest about which parts went, but never truncated.
    """
    if not context:
        return "{}"
    encoded = json.dumps(context, ensure_ascii=False)
    if len(encoded) <= CONTEXT_MAX:
        return encoded
    oversized = len(encoded)

    # Re-parsing is the cheapest deep copy: the caller's dict is not ours to
    # edit, and it is still being used to answer the question.
    shed = json.loads(encoded)
    gatherings = shed.get("gatherings")
    if isinstance(gatherings, list):
        dropped: List[str] = []
        for key in CONTEXT_SHED_ORDER:
            present = False
            for gathering in gatherings:
                if isinstance(gathering, dict) and gathering.pop(key, None) is not None:
                    present = True
            if not present:
                continue
            dropped.append(key)
            shed["dropped"] = list(dropped)
            encoded = json.dumps(shed, ensure_ascii=False)
            if len(encoded) <= CONTEXT_MAX:
                return encoded
        trimmed = _trim_texts(shed, gatherings)
        if trimmed is not None:
            return trimmed

    # A context of some other shape, or one whose text alone cannot be made to
    # fit. Keep the fact that there was one rather than storing something
    # unreadable. This is a fixed, small document, so it needs no budget.
    return json.dumps(
        {"gatherings": [], "dropped": ["all"],
         "note": f"context of {oversized} characters exceeded the "
                 f"{CONTEXT_MAX} this column holds"},
        ensure_ascii=False)


def _trim_texts(shed: Dict[str, Any], gatherings: List[Any]) -> Optional[str]:
    """Fit by shortening the assembled text rather than losing it.

    Truncating a JSON *string value* is safe where truncating the serialised
    document is not - the document stays well formed, and only the string is
    shorter. So the text that section 14.8 wants kept verbatim is cut down to
    a marked prefix instead of being dropped: some of what the model was
    actually given beats none of it, and the marker is what stops a prefix
    from reading as the whole thing.

    Returns the encoded document, or None if even an empty text will not fit.
    """
    holders = [g for g in gatherings if isinstance(g, dict) and g.get("text")]
    if not holders:
        return None
    originals = [str(g["text"]) for g in holders]
    total = sum(len(text) for text in originals)
    shed["text_truncated"] = True
    cut = 0
    while cut < total:
        for holder, original in zip(holders, originals):
            # Taken from each in proportion to its size, so one long gathering
            # does not survive intact while the others are erased.
            keep = len(original) - round(cut * len(original) / total)
            holder["text"] = original[:keep] + TEXT_TRUNCATED_MARKER if keep > 0 else ""
        encoded = json.dumps(shed, ensure_ascii=False)
        excess = len(encoded) - CONTEXT_MAX
        if excess <= 0:
            return encoded
        # A margin on top of the overshoot: escaping means the serialised
        # length does not fall by exactly what is taken off the strings.
        cut += excess + 1024
    return None


def squash(value: float, ceiling: float = WEIGHT_MAX) -> float:
    """A weight, kept inside a fixed range however often it is reinforced.

    tanh is used rather than a hard clamp because it is smooth: a weight near
    the ceiling still moves, just less, so an edge can go on accumulating
    evidence without the number running away or stopping dead at a wall.
    """
    if ceiling <= 0:
        return 0.0
    return ceiling * math.tanh(float(value) / ceiling)


def unsquash(weight: float, ceiling: float = WEIGHT_MAX) -> float:
    """The evidence a given weight implies - the inverse of squash().

    Needed only for connections that predate the accumulator: recovering the
    sum from the weight keeps their history rather than restarting them.
    """
    if ceiling <= 0:
        return 0.0
    ratio = max(-0.999999, min(0.999999, float(weight) / ceiling))
    return ceiling * math.atanh(ratio)


class UsageLog:
    """What each answer was built from, until it can be judged.

    Kept in its own table rather than as activity: this is the agent's own
    record of its own retrieval, it is consumed once, and it should not be
    turned into nodes describing the user.
    """

    def __init__(self, db: Any) -> None:
        self.db = db
        #: Rows already reported as unreadable. That a stored context cannot
        #: be read is a fact about the row, not about this reading of it, and
        #: the Debugger polls recent() every few seconds - so without this,
        #: one corrupt row becomes several warnings a minute for as long as it
        #: stays in the window, and buries the warnings worth reading.
        self._unreadable: Set[str] = set()

    def record(self, question: str, answer: str, node_ids: Sequence[str],
               edge_ids: Sequence[str], task_id: str = "",
               context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        nodes = [i for i in node_ids if i]
        edges = [i for i in edge_ids if i]
        if not nodes and not edges:
            return None
        identifier = new_id("use")
        self.db.execute(
            "INSERT INTO context_uses (id, created_at, task_id, question, answer, "
            "node_ids, edge_ids, context, processed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (identifier, datetime.now(timezone.utc).isoformat(), task_id,
             (question or "")[:2000], (answer or "")[:4000],
             json.dumps(nodes), json.dumps(edges),
             encode_context(context)),
        )
        return identifier

    def _decode_context(self, row: Any) -> Dict[str, Any]:
        """The stored context, or nothing if this row's cannot be read.

        The context is the Debugger's view of how an answer was assembled;
        reinforcement judges an answer from its question, its answer and the
        ids, and never looks at it. So a row whose context is unreadable is
        still perfectly good evidence, and must go on being learned from.

        Section 14.8 already requires that "a failure to record it is not a
        failure of the turn". The same has to hold on the way back out, and it
        did not: rows written before ``encode_context`` hold truncated JSON,
        and the parse happened while assembling the batch - so one such row
        raised before anything was marked processed, the whole pass died, and
        the next pass began by reading the same row again. An unreadable view
        of one answer stopped all learning, permanently, and it stayed stopped
        until someone read the log.
        """
        raw = row["context"] if "context" in row.keys() else None
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            identifier = str(row["id"])
            if identifier not in self._unreadable:
                if len(self._unreadable) >= UNREADABLE_REPORTED_MAX:
                    # Bounded rather than grown without limit. Forgetting means
                    # a row may be reported twice over a long run, which is the
                    # harmless direction to err in.
                    self._unreadable.clear()
                self._unreadable.add(identifier)
                log.warning("the stored context for %s cannot be read; using "
                            "the answer without it", identifier)
            return {}

    def pending(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM context_uses WHERE processed = 0 ORDER BY created_at LIMIT ?",
            (limit,))
        out = []
        for row in rows:
            out.append({
                "id": row["id"], "created_at": row["created_at"],
                "task_id": row["task_id"], "question": row["question"],
                "answer": row["answer"],
                "node_ids": json.loads(row["node_ids"] or "[]"),
                "edge_ids": json.loads(row["edge_ids"] or "[]"),
                "context": self._decode_context(row),
            })
        return out

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """The last few answers and what each was built from (section 14.8)."""
        rows = self.db.query(
            "SELECT * FROM context_uses ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for row in rows:
            out.append({
                "id": row["id"], "created_at": row["created_at"],
                "task_id": row["task_id"], "question": row["question"],
                "answer": row["answer"],
                "processed": bool(row["processed"]),
                "context": self._decode_context(row),
            })
        return out

    def mark_processed(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        self.db.execute(
            f"UPDATE context_uses SET processed = 1 WHERE id IN ({placeholders})",
            list(ids))

    def pending_count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM context_uses WHERE processed = 0")
        return int(row["c"]) if row else 0


class Reinforcer:
    """Moves weights towards what actually helped."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    def reward(self, edge_id: str, helped: bool) -> Optional[float]:
        """Record one more piece of evidence about a connection.

        The evidence is what moves; the weight follows from it. Returns the
        new weight, or None if the edge has gone - an edge can be deleted
        between being walked and being judged, and that is not an error worth
        failing a heartbeat over.
        """
        edge = self.graph.get_edge(edge_id) if hasattr(self.graph, "get_edge") else None
        if edge is None:
            return None
        evidence = self.graph.evidence_of(edge_id)
        if evidence is None:
            # An edge from before this was recorded: recover the sum its
            # weight implies, so that its history is not restarted at zero.
            evidence = unsquash(float(edge.weight))
        evidence += STEP_UP if helped else -STEP_DOWN
        # A connection is never pushed to nothing: it stays walkable, so that
        # a wrong judgement now can be corrected by a right one later.
        weight = max(squash(evidence), 0.01)
        self.graph.set_edge_weight(edge_id, weight, evidence=evidence)
        return weight

    def reward_many(self, judgements: Dict[str, bool]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for edge_id, helped in judgements.items():
            new_weight = self.reward(edge_id, helped)
            if new_weight is not None:
                out[edge_id] = new_weight
        return out


__all__ = ["UsageLog", "Reinforcer", "squash", "unsquash", "WEIGHT_MAX", "STEP_UP", "STEP_DOWN",
           "LONG_CONTENT", "LONG_SUMMARY"]
