"""Knowledge Graph storage and traversal primitives (sections 38-40).

A node lookup exposes enough information to make a retrieval decision without
loading the destination nodes themselves.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...common.events.models import (
    EdgeView,
    KnowledgeEdge,
    KnowledgeNode,
    iso,
    json_dump,
    utcnow,
)
from ..storage.database import Database


#: Scripts written without spaces between words. Splitting on whitespace
#: leaves a whole sentence as one token, and no stored text contains a whole
#: sentence, so every search in these languages matches nothing at all.
CONTINUOUS_SCRIPTS = (
    ("一", "鿿"),      # Chinese
    ("぀", "ヿ"),      # Japanese kana
    ("가", "힯"),      # Korean
)

#: How many terms one query contributes. More is slower and not more accurate.
MAX_SEARCH_TERMS = 6


def _continuous(character: str) -> bool:
    return any(low <= character <= high for low, high in CONTINUOUS_SCRIPTS)


def search_terms(query: str) -> List[str]:
    """The pieces of a query worth looking for in stored text.

    Splitting on whitespace is right for languages that put spaces between
    words and useless for those that do not: a Chinese question arrives as a
    single token, nothing in the graph contains that whole sentence, and the
    search returns nothing - so the agent answers with no context at all and
    nothing says why.

    Words are used where there are words. Where there are not, overlapping
    pairs of characters are used instead: a pair is short enough to occur in
    stored text and long enough to mean something, which is the usual way to
    search these scripts without a dictionary to segment them.
    """
    text = str(query or "").lower()
    terms: List[str] = []
    run: List[str] = []

    def flush_run() -> None:
        if not run:
            return
        block = "".join(run)
        run.clear()
        if len(block) == 1:
            terms.append(block)
            return
        for index in range(len(block) - 1):
            terms.append(block[index:index + 2])

    for character in text:
        if _continuous(character):
            run.append(character)
            continue
        flush_run()
        if character.isspace():
            terms.append(" ")
        else:
            terms.append(character)
    flush_run()

    # Re-join the runs of ordinary characters into words.
    words: List[str] = []
    current: List[str] = []
    for term in terms:
        if len(term) == 1 and not _continuous(term):
            if term == " ":
                if current:
                    words.append("".join(current))
                    current = []
            else:
                current.append(term)
        else:
            if current:
                words.append("".join(current))
                current = []
            words.append(term)
    if current:
        words.append("".join(current))

    seen: List[str] = []
    for word in words:
        word = word.strip()
        if len(word) < 2 or word in seen:
            continue
        seen.append(word)
        if len(seen) >= MAX_SEARCH_TERMS:
            break
    return seen


#: How many words one node may be found by. Beyond a handful they stop
#: distinguishing anything: a node that matches everything is a node that
#: is always returned, which is the same as having no keywords at all.
MAX_KEYWORDS = 12

#: What a keyword hit is worth against a word merely appearing in the
#: text. Someone chose the keyword; the text happened.
KEYWORD_WEIGHT = 3


class GraphStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- nodes --------------------------------------------------------------
    def upsert_node(self, node: KnowledgeNode, key: Optional[str] = None) -> KnowledgeNode:
        existing = self.get_node_by_key(key) if key else self.get_node(node.id)
        now = utcnow()
        if existing:
            node.id = existing.id
            node.created_at = existing.created_at
            node.updated_at = now
            self.db.execute(
                """
                UPDATE nodes SET type = ?, title = ?, summary = ?, content = ?,
                    updated_at = ?, source = ?, metadata = ?, keywords = ?
                WHERE id = ?
                """,
                (
                    node.type, node.title, node.summary, node.content, iso(now),
                    node.source, json_dump(node.metadata),
                    # Keywords are maintained separately (section 51.1), so a
                    # rewrite that carries none must not silently erase them.
                    json_dump(node.keywords or existing.keywords), node.id,
                ),
            )
            return node
        node.created_at = node.created_at or now
        node.updated_at = now
        self.db.execute(
            """
            INSERT INTO nodes (id, node_key, type, title, summary, content,
                               created_at, updated_at, source, metadata, keywords)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node.id, key, node.type, node.title, node.summary, node.content,
                iso(node.created_at), iso(node.updated_at), node.source,
                json_dump(node.metadata), json_dump(node.keywords),
            ),
        )
        return node

    # -- keywords (section 51.1) --------------------------------------------
    def set_keywords(self, node_id: str, keywords: List[str]) -> List[str]:
        """Replace what a node should be found by.

        Kept apart from the node's text so they can be revised without
        rewriting what it says - the words someone would look for it under
        change as they work, while what it records does not.
        """
        cleaned: List[str] = []
        for word in keywords:
            word = str(word or "").strip().lower()[:60]
            if word and word not in cleaned:
                cleaned.append(word)
        cleaned = cleaned[:MAX_KEYWORDS]
        self.db.execute(
            "UPDATE nodes SET keywords = ?, updated_at = ? WHERE id = ?",
            (json_dump(cleaned), iso(utcnow()), node_id))
        return cleaned

    def revise_keywords(self, node_id: str, add: Optional[List[str]] = None,
                        remove: Optional[List[str]] = None) -> List[str]:
        """Add and drop words, rather than replacing the whole set.

        A round of processing sees one slice of the user's activity, and
        should be able to say "this is also about X" without having to restate
        everything the node was already known by.
        """
        node = self.get_node(node_id)
        if node is None:
            return []
        gone = {str(word).strip().lower() for word in (remove or [])}
        keywords = [word for word in node.keywords if word not in gone]
        keywords.extend(str(word).strip().lower() for word in (add or []))
        return self.set_keywords(node_id, keywords)

    def get_node(self, node_id: str) -> Optional[KnowledgeNode]:
        row = self.db.query_one("SELECT * FROM nodes WHERE id = ?", (node_id,))
        return KnowledgeNode.from_row(row) if row else None

    def get_node_by_key(self, key: Optional[str]) -> Optional[KnowledgeNode]:
        if not key:
            return None
        row = self.db.query_one("SELECT * FROM nodes WHERE node_key = ?", (key,))
        return KnowledgeNode.from_row(row) if row else None

    def delete_node(self, node_id: str) -> None:
        self.db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))

    def node_count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM nodes")
        return int(row["c"]) if row else 0

    def edge_count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM edges")
        return int(row["c"]) if row else 0

    def list_nodes(self, limit: int = 200, offset: int = 0,
                   node_type: Optional[str] = None) -> List[KnowledgeNode]:
        if node_type:
            rows = self.db.query(
                "SELECT * FROM nodes WHERE type = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (node_type, limit, offset),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM nodes ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)
            )
        return [KnowledgeNode.from_row(r) for r in rows]

    def search_nodes(self, query: str, limit: int = 20) -> List[KnowledgeNode]:
        """Keyword search used to pick candidate starting nodes (section 42.1)."""
        terms = search_terms(query)
        if not terms:
            return self.list_nodes(limit=limit)
        scored: Dict[str, tuple] = {}
        for term in terms:
            like = f"%{term}%"
            rows = self.db.query(
                """
                SELECT * FROM nodes
                WHERE lower(title) LIKE ? OR lower(summary) LIKE ?
                   OR lower(content) LIKE ? OR lower(keywords) LIKE ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (like, like, like, like, limit * 4),
            )
            for row in rows:
                node = KnowledgeNode.from_row(row)
                haystack = f"{node.title} {node.summary} {node.content}".lower()
                hits = haystack.count(term)
                if any(term in keyword for keyword in node.keywords):
                    hits += KEYWORD_WEIGHT
                score, _ = scored.get(node.id, (0, None))
                scored[node.id] = (score + hits, node)
        ranked = sorted(scored.values(), key=lambda item: item[0], reverse=True)
        return [node for _, node in ranked[:limit]]


    def search_in_category(self, query: str, category: str,
                           limit: int = 8) -> List[tuple]:
        """Nodes of one kind that share words with the question, best first.

        Used only to choose where a walk begins. The walk itself is drawn from
        the weights, but beginning somewhere the question actually mentions is
        strictly better than beginning nowhere in particular - and it costs
        one query, once per route.
        """
        from ..retrieval.traverser import OTHER, USER_NATURE

        terms = search_terms(query)
        if not terms:
            return []
        if category == OTHER:
            where, params = " AND type != ?", [USER_NATURE]
        else:
            where, params = " AND type = ?", [category]
        scored: Dict[str, tuple] = {}
        for term in terms:
            like = "%" + term + "%"
            rows = self.db.query(
                "SELECT * FROM nodes WHERE (lower(title) LIKE ? OR lower(summary) "
                "LIKE ? OR lower(content) LIKE ? OR lower(keywords) LIKE ?)"
                + where + " ORDER BY updated_at DESC LIMIT ?",
                [like, like, like, like] + params + [limit * 4])
            for row in rows:
                node = KnowledgeNode.from_row(row)
                haystack = (node.title + " " + node.summary + " " + node.content).lower()
                # A keyword is a deliberate statement that this node is about
                # that word, so it is worth more than the same word happening
                # to appear somewhere in the text (section 51.1).
                hits = haystack.count(term)
                if any(term in keyword for keyword in node.keywords):
                    hits += KEYWORD_WEIGHT
                score, _ = scored.get(node.id, (0, None))
                scored[node.id] = (score + hits, node)
        ranked = sorted(scored.values(), key=lambda item: item[0], reverse=True)
        return [(score, node) for score, node in ranked[:limit] if score > 0]

    # -- random selection (section 42.1, as amended) ------------------------
    def random_node(self, node_type: Optional[str] = None,
                    excluding_type: Optional[str] = None,
                    excluding_ids: Optional[List[str]] = None,
                    category: Optional[str] = None) -> Optional[KnowledgeNode]:
        """One node, chosen uniformly at random.

        Chosen by the database rather than by reading the nodes and picking
        one here: the graph is the thing that grows, and a walk that starts by
        loading all of it would cost more than the walk itself.
        """
        from ..retrieval.traverser import OTHER, USER_NATURE

        sql = "SELECT * FROM nodes WHERE 1=1"
        params: List[Any] = []
        if category:
            # "Everything else" is defined by exclusion: any type that is not
            # one of the two named kinds belongs to it, including types this
            # version has never heard of.
            if category == OTHER:
                sql += " AND type != ?"
                params.append(USER_NATURE)
            else:
                sql += " AND type = ?"
                params.append(category)
        if node_type:
            sql += " AND type = ?"
            params.append(node_type)
        if excluding_type:
            sql += " AND type != ?"
            params.append(excluding_type)
        ids = [i for i in (excluding_ids or []) if i]
        if ids:
            sql += f" AND id NOT IN ({', '.join('?' for _ in ids)})"
            params.extend(ids)
        sql += " ORDER BY RANDOM() LIMIT 1"
        row = self.db.query_one(sql, params)
        return KnowledgeNode.from_row(row) if row else None

    # -- edges --------------------------------------------------------------
    def upsert_edge(self, edge: KnowledgeEdge) -> KnowledgeEdge:
        row = self.db.query_one(
            """
            SELECT * FROM edges
            WHERE source_node_id = ? AND target_node_id = ? AND relationship_type = ?
            """,
            (edge.source_node_id, edge.target_node_id, edge.relationship_type),
        )
        now = utcnow()
        if row:
            existing = KnowledgeEdge.from_row(row)
            log_entries = existing.relationship_log + (edge.relationship_log or [])
            summary = edge.summary or existing.summary
            weight = edge.weight if edge.weight is not None else existing.weight
            self.db.execute(
                """
                UPDATE edges SET summary = ?, weight = ?, updated_at = ?, relationship_log = ?,
                    metadata = ? WHERE id = ?
                """,
                (
                    summary, weight, iso(now), json_dump(log_entries),
                    json_dump({**existing.metadata, **edge.metadata}), existing.id,
                ),
            )
            existing.summary = summary
            existing.weight = weight
            existing.relationship_log = log_entries
            existing.updated_at = now
            return existing
        edge.created_at = edge.created_at or now
        edge.updated_at = now
        self.db.execute(
            """
            INSERT INTO edges (id, source_node_id, target_node_id, relationship_type, summary,
                               weight, created_at, updated_at, relationship_log, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.id, edge.source_node_id, edge.target_node_id, edge.relationship_type,
                edge.summary, edge.weight, iso(edge.created_at), iso(edge.updated_at),
                json_dump(edge.relationship_log), json_dump(edge.metadata),
            ),
        )
        return edge

    def set_edge_weight(self, edge_id: str, weight: float,
                        evidence: Optional[float] = None) -> None:
        """Section 44.1, as amended: what proved related is what gets walked.

        The evidence is the record; the weight is the bounded view of it that
        sampling uses. They are written together so the two cannot drift.
        """
        self.db.execute(
            "UPDATE edges SET weight = ?, evidence = ?, updated_at = ? WHERE id = ?",
            (float(weight),
             None if evidence is None else float(evidence),
             datetime.now(timezone.utc).isoformat(), edge_id))

    def evidence_of(self, edge_id: str) -> Optional[float]:
        """How much this connection has proved itself, or None if never judged."""
        row = self.db.query_one("SELECT evidence FROM edges WHERE id = ?", (edge_id,))
        if row is None:
            return None
        value = row["evidence"] if "evidence" in row.keys() else None
        return None if value is None else float(value)

    def get_edge(self, edge_id: str) -> Optional[KnowledgeEdge]:
        row = self.db.query_one("SELECT * FROM edges WHERE id = ?", (edge_id,))
        return KnowledgeEdge.from_row(row) if row else None

    def delete_edge(self, edge_id: str) -> None:
        self.db.execute("DELETE FROM edges WHERE id = ?", (edge_id,))

    def outgoing(self, node_id: str, limit: int = 50) -> List[EdgeView]:
        """Section 40 - neighbour summaries without loading full nodes."""
        rows = self.db.query(
            """
            SELECT e.id AS edge_id, e.relationship_type, e.summary AS rel_summary, e.weight,
                   e.metadata AS edge_metadata,
                   n.id AS node_id, n.title, n.type, n.summary AS node_summary
            FROM edges e JOIN nodes n ON n.id = e.target_node_id
            WHERE e.source_node_id = ?
            ORDER BY e.weight DESC, e.updated_at DESC LIMIT ?
            """,
            (node_id, limit),
        )
        from ...common.events.models import json_load

        return [
            EdgeView(
                edge_id=r["edge_id"],
                target_node_id=r["node_id"],
                target_title=r["title"],
                target_type=r["type"],
                target_summary=r["node_summary"],
                relationship_type=r["relationship_type"],
                relationship_summary=r["rel_summary"],
                weight=float(r["weight"]),
                metadata=json_load(r["edge_metadata"]),
            )
            for r in rows
        ]

    def incoming(self, node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self.db.query(
            """
            SELECT e.id AS edge_id, e.relationship_type, e.summary, e.weight,
                   n.id AS node_id, n.title, n.type, n.summary AS node_summary
            FROM edges e JOIN nodes n ON n.id = e.source_node_id
            WHERE e.target_node_id = ?
            ORDER BY e.weight DESC LIMIT ?
            """,
            (node_id, limit),
        )

    def connected_edges(self, node_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Every edge touching a node, in both directions (section 13).

        One shape for both directions, carrying the text written on the edge -
        the relationship summary and its log - so inspecting a node shows what
        the relationship actually says rather than only that one exists.
        """
        from ...common.events.models import json_load

        rows = self.db.query(
            """
            SELECT e.id AS edge_id, e.relationship_type, e.summary AS relationship_summary,
                   e.weight, e.created_at, e.updated_at,
                   e.relationship_log, e.metadata,
                   'outgoing' AS direction,
                   n.id AS other_node_id, n.title AS other_title, n.type AS other_type,
                   n.summary AS other_summary
            FROM edges e JOIN nodes n ON n.id = e.target_node_id
            WHERE e.source_node_id = ?
            UNION ALL
            SELECT e.id AS edge_id, e.relationship_type, e.summary AS relationship_summary,
                   e.weight, e.created_at, e.updated_at,
                   e.relationship_log, e.metadata,
                   'incoming' AS direction,
                   n.id AS other_node_id, n.title AS other_title, n.type AS other_type,
                   n.summary AS other_summary
            FROM edges e JOIN nodes n ON n.id = e.source_node_id
            WHERE e.target_node_id = ?
            ORDER BY weight DESC, edge_id
            LIMIT ?
            """,
            (node_id, node_id, limit),
        )
        edges: List[Dict[str, Any]] = []
        for row in rows:
            edge = dict(row)
            edge["relationship_log"] = json_load(edge.get("relationship_log")) or []
            edge["metadata"] = json_load(edge.get("metadata")) or {}
            edge["weight"] = float(edge["weight"])
            edges.append(edge)
        return edges

    # -- visualisation ------------------------------------------------------
    def subgraph(self, center: Optional[str] = None, depth: int = 1,
                 limit: int = 250) -> Dict[str, Any]:
        """Neighbourhood based rendering (section 13): never the whole graph."""
        if center:
            visited = {center}
            frontier = [center]
            for _ in range(max(0, depth)):
                next_frontier: List[str] = []
                for node_id in frontier:
                    for view in self.outgoing(node_id, limit=25):
                        if view.target_node_id not in visited:
                            visited.add(view.target_node_id)
                            next_frontier.append(view.target_node_id)
                    for row in self.incoming(node_id, limit=25):
                        if row["node_id"] not in visited:
                            visited.add(row["node_id"])
                            next_frontier.append(row["node_id"])
                frontier = next_frontier
                if len(visited) >= limit:
                    break
            node_ids = list(visited)[:limit]
        else:
            node_ids = [n.id for n in self.list_nodes(limit=limit)]
        if not node_ids:
            return {"nodes": [], "edges": [], "truncated": False,
                    "total_nodes": self.node_count(), "total_edges": self.edge_count()}
        placeholders = ",".join("?" for _ in node_ids)
        node_rows = self.db.query(
            f"SELECT * FROM nodes WHERE id IN ({placeholders})", node_ids
        )
        edge_rows = self.db.query(
            f"""
            SELECT * FROM edges
            WHERE source_node_id IN ({placeholders}) AND target_node_id IN ({placeholders})
            """,
            node_ids + node_ids,
        )
        nodes = [KnowledgeNode.from_row(r) for r in node_rows]
        edges = [KnowledgeEdge.from_row(r) for r in edge_rows]
        return {
            "nodes": [
                {**n.to_dict(), "content": (n.content[:400] + "...") if len(n.content) > 400 else n.content}
                for n in nodes
            ],
            "edges": [e.to_dict() for e in edges],
            "truncated": self.node_count() > len(nodes),
            "total_nodes": self.node_count(),
            "total_edges": self.edge_count(),
        }

    def node_detail(self, node_id: str) -> Optional[Dict[str, Any]]:
        node = self.get_node(node_id)
        if node is None:
            return None
        edges = self.connected_edges(node_id)
        return {
            "node": node.to_dict(),
            "outgoing": [v.to_dict() for v in self.outgoing(node_id)],
            "incoming": self.incoming(node_id),
            # Section 13, as amended: every connected edge, both directions,
            # each carrying the text written on the relationship.
            "edges": edges,
            "edge_count": len(edges),
        }

    def stats(self) -> Dict[str, Any]:
        types = self.db.query("SELECT type, COUNT(*) AS c FROM nodes GROUP BY type ORDER BY c DESC")
        rels = self.db.query(
            "SELECT relationship_type, COUNT(*) AS c FROM edges GROUP BY relationship_type ORDER BY c DESC"
        )
        return {
            "nodes": self.node_count(),
            "edges": self.edge_count(),
            "node_types": types,
            "relationship_types": rels,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
