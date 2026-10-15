"""Keeping a node to a readable size (section 50, as amended).

Compaction used to be a pass over the graph: find the nodes that had grown
past the limit, cut them back, and file the original away. It had to be,
because nodes grew - a round that recognised an existing node appended its
text to it, so a handful of nodes accumulated indefinitely and something had
to come along behind and trim them.

Nodes do not grow any more. A round writes new ones (section 49.5, as
amended), each a self-contained record of what it saw, and nothing is ever
appended to a node after it is stored. So the only way a node can be too long
is to arrive that way, and the moment to deal with that is the moment it
arrives.

Which makes this a **write-time guard** rather than a sweep, and removes the
part of the old design that was hardest to defend: compaction used to
re-summarise its own summaries. A node cut back in August grew again, was cut
back again in September from the already-compressed text, and the archive then
held a compressed snapshot of a compressed snapshot. Fitting once, on the way
in, means what is archived is always the original the model actually wrote.

The original is still kept. A node is summarised for the graph, not censored:
the full text goes into an archive node that retrieval can reach but rarely
walks to, joined by a deliberately weak edge.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from ..common.events.models import KnowledgeEdge, KnowledgeNode
from .graph.store import GraphStore

log = logging.getLogger(__name__)

ARCHIVE_TYPE = "archive"
ARCHIVE_RELATIONSHIP = "archived_history_of"

#: The weight on the edge from an archive back to the node it came from.
#: Low on purpose: the archive is there to be findable, not to be walked in
#: the ordinary course of answering something.
ARCHIVE_EDGE_WEIGHT = 0.3

#: The two headings a fitted node carries, counted against the limit so that
#: the fitted node is within it including its own scaffolding.
_MARKERS = "[summarised to fit]\n\n\n[most recent detail]\n"


class Compressor:
    """Fits a node to the size limit as it is written."""

    def __init__(self, graph: GraphStore, config: Any, summarizer=None) -> None:
        self.graph = graph
        self.config = config
        #: ``summarizer(text, hint) -> str``, normally the data heartbeat's
        #: model. Without it the fallback is structural, so a node is still
        #: fitted when no model is reachable.
        self.summarizer = summarizer

    @property
    def node_limit(self) -> int:
        return int(self.config.get("node_content_max_chars", 8000))

    def fits(self, node: KnowledgeNode) -> bool:
        return len(node.content or "") <= self.node_limit

    def fit(self, node: KnowledgeNode) -> Tuple[KnowledgeNode, Optional[Dict[str, Any]]]:
        """Return the node as it should be stored, and what was set aside.

        The node is modified in place and returned, so a caller that does not
        care about the archive can ignore the second value entirely. A node
        already within the limit is returned untouched and nothing is written.
        """
        if not bool(self.config.get("compression_enabled", True)):
            return node, None
        if self.fits(node):
            return node, None

        original = node.content
        # The tail is kept verbatim because it is the most recent thing the
        # node says, and a summary is worst exactly where detail matters most.
        keep_tail = original[-self.node_limit // 2:]
        compressed = self._summarise(node, original)
        # A summary is a request, not a guarantee. A model can return more than
        # it was given, the structural fallback returns a tenth of the lines
        # which is no smaller for a node that is one long line, and either way
        # nothing above checks. Without this the guard reports success and
        # stores a node four times over the limit - the failure the whole
        # function exists to prevent, arrived at through the path that runs
        # when the model is away.
        headroom = self.node_limit - len(keep_tail) - len(_MARKERS)
        if len(compressed) > headroom:
            compressed = compressed[:max(0, headroom)]

        archive = self.graph.upsert_node(
            KnowledgeNode(
                type=ARCHIVE_TYPE,
                title=f"Archive: {node.title}"[:200],
                summary="the full text of a node that was summarised to fit",
                content=original,
                source="data_heartbeat",
                metadata={"archived_from_title": node.title,
                          "original_length": len(original)},
            )
        )
        node.content = (f"[summarised to fit]\n{compressed}\n\n"
                        f"[most recent detail]\n{keep_tail}")
        node.metadata = {**node.metadata, "compressed": True,
                         "archive_node": archive.id,
                         "original_length": len(original)}
        return node, {"archive_node_id": archive.id,
                      "original_length": len(original),
                      "new_length": len(node.content)}

    def link_archive(self, node: KnowledgeNode, archive_id: str) -> None:
        """Join the archive to the stored node.

        Separate from ``fit`` because the node has no id until it is written,
        and an edge to an id that does not exist yet is an edge to nothing.
        """
        if not archive_id or not node.id:
            return
        self.graph.upsert_edge(KnowledgeEdge(
            source_node_id=archive_id,
            target_node_id=node.id,
            relationship_type=ARCHIVE_RELATIONSHIP,
            summary="the full text, kept alongside the summarised form",
            weight=ARCHIVE_EDGE_WEIGHT,
        ))

    # -- internals ----------------------------------------------------------
    def _summarise(self, node: KnowledgeNode, original: str) -> str:
        if self.summarizer is not None:
            try:
                answer = self.summarizer(
                    original[: self.node_limit * 2],
                    f"Compress the record '{node.title}' into the essential "
                    "facts, decisions and identifiers.")
                if answer and answer.strip():
                    return answer.strip()
            except Exception:
                log.debug("summarizer failed; falling back to structural fitting",
                          exc_info=True)
        # No model, or nothing usable back. Structural trimming is blunt, and
        # it is the difference between a fitted node and a failed write.
        lines = [line for line in original.splitlines() if line.strip()]
        return "\n".join(lines[: max(5, len(lines) // 10)])


__all__ = ["Compressor", "ARCHIVE_TYPE", "ARCHIVE_RELATIONSHIP",
           "ARCHIVE_EDGE_WEIGHT"]
